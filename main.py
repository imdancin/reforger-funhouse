"""Server Launch Automation - Orchestrates Arma Reforger server provisioning and monitoring."""

import signal
import subprocess
import sys
import time
import socket
from pathlib import Path
from typing import NoReturn

import paramiko

# ---------------------------------------------------------------------------
# Configuration Constants
# ---------------------------------------------------------------------------

SSH_RETRY_INTERVAL = 10.0  # seconds between SSH connection attempts
SSH_TIMEOUT = 300.0  # max seconds to wait for SSH
LOG_FILE_WAIT_TIMEOUT = 60.0  # max seconds to wait for cloud-init log
POD_POLL_INTERVAL = 5.0  # seconds between pod status checks
POD_TIMEOUT = 300.0  # max seconds to wait for pod Running
SSH_KEY_PATH = Path.home() / ".ssh" / "id_ed25519"
SSH_USERNAME = "ubuntu"
TERRAFORM_OUTPUT_KEY = "arma_server_public_ip"
BOOTSTRAP_MARKER = "=== K3s, ArgoCD, and Arma Reforger Bootstrap Complete ==="
POD_LABEL_SELECTOR = "app=arma-server"
POD_CONTAINER_NAME = "reforger"
LOG_FILE_PATH = "/var/log/cloud-init-output.log"

# ---------------------------------------------------------------------------
# Global state for signal handler cleanup
# ---------------------------------------------------------------------------

_ssh_client: paramiko.SSHClient | None = None
_terraform_process: subprocess.Popen | None = None


# ---------------------------------------------------------------------------
# Signal Handler
# ---------------------------------------------------------------------------

def _handle_sigint(sig: int, frame) -> None:
    """Handle SIGINT (Ctrl+C) by cleaning up active resources and exiting."""
    global _ssh_client, _terraform_process
    print("\nShutting down...")
    if _terraform_process and _terraform_process.poll() is None:
        _terraform_process.terminate()
        _terraform_process.wait(timeout=5)
    if _ssh_client:
        _ssh_client.close()
    sys.exit(0)


# ---------------------------------------------------------------------------
# TerraformRunner
# ---------------------------------------------------------------------------

class TerraformRunner:
    """Executes terraform apply and extracts the server IP."""

    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root

    def run(self) -> str:
        """
        Run terraform apply -auto-approve, stream output, extract IP.
        Returns the public IP string.
        Raises SystemExit on failure.
        """
        global _terraform_process

        process = subprocess.Popen(
            ["terraform", "apply", "-auto-approve"],
            cwd=self._project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        _terraform_process = process

        self._stream_output(process)
        process.wait()

        _terraform_process = None

        if process.returncode != 0:
            print(f"Terraform apply failed with exit code {process.returncode}")
            sys.exit(1)

        return self._extract_ip()

    def _stream_output(self, process: subprocess.Popen) -> None:
        """Stream stdout/stderr line-by-line to console."""
        for line in iter(process.stdout.readline, b""):
            print(line.decode("utf-8", errors="replace"), end="")
        process.stdout.close()

    def _extract_ip(self) -> str:
        """
        Run terraform output -raw arma_server_public_ip.
        Returns IP string or raises SystemExit if missing.
        """
        result = subprocess.run(
            ["terraform", "output", "-raw", TERRAFORM_OUTPUT_KEY],
            cwd=self._project_root,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0 or not result.stdout.strip():
            print(f"Error: Terraform output key '{TERRAFORM_OUTPUT_KEY}' not found")
            sys.exit(1)

        return result.stdout.strip()


# ---------------------------------------------------------------------------
# SSHMonitor
# ---------------------------------------------------------------------------

class SSHMonitor:
    """Establishes SSH connection with retry logic."""

    def __init__(
        self,
        host: str,
        key_path: Path = SSH_KEY_PATH,
        username: str = SSH_USERNAME,
        retry_interval: float = SSH_RETRY_INTERVAL,
        timeout: float = SSH_TIMEOUT,
    ) -> None:
        self.host = host
        self.key_path = key_path
        self.username = username
        self.retry_interval = retry_interval
        self.timeout = timeout

    def connect(self) -> paramiko.SSHClient:
        """
        Poll port 22 until reachable, then establish SSH connection.
        Returns connected SSHClient.
        Raises SystemExit on timeout or auth failure.
        """
        global _ssh_client

        self._wait_for_port()

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            private_key = paramiko.Ed25519Key.from_private_key_file(str(self.key_path))
        except FileNotFoundError:
            print(f"Error: SSH key file not found: {self.key_path}")
            sys.exit(1)

        try:
            client.connect(
                hostname=self.host,
                username=self.username,
                pkey=private_key,
            )
        except paramiko.AuthenticationException:
            print(f"Error: SSH authentication failed for {self.username}@{self.host}")
            sys.exit(1)

        _ssh_client = client
        return client

    def _wait_for_port(self) -> None:
        """TCP socket poll loop with status messages."""
        start_time = time.time()
        attempt = 0

        while True:
            attempt += 1
            elapsed = time.time() - start_time

            if elapsed > self.timeout:
                print(
                    f"Error: Timed out waiting for SSH on {self.host} "
                    f"after {elapsed:.1f} seconds"
                )
                sys.exit(1)

            try:
                conn = socket.create_connection((self.host, 22), timeout=5)
                conn.close()
                return
            except (socket.timeout, socket.error, OSError):
                print(
                    f"SSH connection attempt {attempt} - "
                    f"elapsed {elapsed:.1f}s - waiting for {self.host}:22..."
                )
                time.sleep(self.retry_interval)


# ---------------------------------------------------------------------------
# StartupLogStreamer
# ---------------------------------------------------------------------------

class StartupLogStreamer:
    """Streams cloud-init logs until bootstrap marker is detected."""

    def __init__(
        self,
        ssh_client: paramiko.SSHClient,
        marker_text: str = BOOTSTRAP_MARKER,
        log_path: str = LOG_FILE_PATH,
        file_wait_timeout: float = LOG_FILE_WAIT_TIMEOUT,
    ) -> None:
        self._ssh_client = ssh_client
        self._marker_text = marker_text
        self._log_path = log_path
        self._file_wait_timeout = file_wait_timeout

    def stream_until_marker(self) -> None:
        """
        Tail the log file, printing lines until marker is found.
        Raises SystemExit if log file not found within timeout.
        """
        self._wait_for_log_file()

        _, stdout, _ = self._ssh_client.exec_command(f"tail -f {self._log_path}")
        channel = stdout.channel

        try:
            while True:
                line = stdout.readline()
                if not line:
                    break
                print(line, end="")
                if self._marker_text in line:
                    break
        except (socket.error, IOError):
            print("Error: SSH connection lost while streaming startup logs")
            sys.exit(1)
        finally:
            channel.close()

    def _wait_for_log_file(self) -> None:
        """Retry checking for log file existence every 5 seconds."""
        start_time = time.time()

        while True:
            elapsed = time.time() - start_time

            if elapsed >= self._file_wait_timeout:
                print(
                    f"Error: Log file {self._log_path} not found "
                    f"after {self._file_wait_timeout:.0f} seconds"
                )
                sys.exit(1)

            _, stdout, _ = self._ssh_client.exec_command(
                f"test -f {self._log_path} && echo EXISTS"
            )
            output = stdout.read().decode("utf-8").strip()

            if output == "EXISTS":
                return

            time.sleep(5)


# ---------------------------------------------------------------------------
# PodLogMonitor
# ---------------------------------------------------------------------------

class PodLogMonitor:
    """Monitors kubernetes pod logs via SSH."""

    def __init__(
        self,
        ssh_client: paramiko.SSHClient,
        label_selector: str = POD_LABEL_SELECTOR,
        container_name: str = POD_CONTAINER_NAME,
        poll_interval: float = POD_POLL_INTERVAL,
        timeout: float = POD_TIMEOUT,
    ) -> None:
        self.ssh_client = ssh_client
        self.label_selector = label_selector
        self.container_name = container_name
        self.poll_interval = poll_interval
        self.timeout = timeout

    def stream_logs(self) -> None:
        """
        Wait for pod to be Running, then stream kubectl logs --follow.
        Raises SystemExit on timeout or connection loss.
        """
        self._wait_for_pod_running()

        cmd = (
            f"kubectl logs --follow -l {self.label_selector} "
            f"-c {self.container_name}"
        )

        try:
            _stdin, stdout, _stderr = self.ssh_client.exec_command(cmd)
            for line in iter(stdout.readline, ""):
                print(line, end="")
        except (socket.error, IOError, OSError):
            print("Error: SSH connection lost while streaming pod logs")
            sys.exit(1)

    def _wait_for_pod_running(self) -> None:
        """Poll pod status until phase is Running."""
        cmd = (
            f"kubectl get pod -l {self.label_selector} "
            f"-o jsonpath='{{.items[0].status.phase}}'"
        )

        start_time = time.time()

        while True:
            elapsed = time.time() - start_time

            if elapsed > self.timeout:
                print(
                    f"Error: Pod did not become available within "
                    f"{self.timeout:.0f} seconds"
                )
                sys.exit(1)

            try:
                _stdin, stdout, _stderr = self.ssh_client.exec_command(cmd)
                phase = stdout.read().decode("utf-8", errors="replace").strip()
            except (socket.error, IOError, OSError):
                print("Error: SSH connection lost while waiting for pod")
                sys.exit(1)

            if phase == "Running":
                return

            print(
                f"Waiting for pod (label={self.label_selector}) - "
                f"current phase: {phase or 'unknown'} - "
                f"elapsed {elapsed:.1f}s..."
            )
            time.sleep(self.poll_interval)


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

def main() -> None:
    """Entry point: wire up signal handler and run pipeline stages."""
    signal.signal(signal.SIGINT, _handle_sigint)

    # Stage 1: Terraform apply — provision infrastructure and extract server IP
    ip = TerraformRunner(Path.cwd()).run()

    # Stage 2: SSH connection — wait for instance and establish session
    client = SSHMonitor(ip).connect()

    # Stage 3: Stream cloud-init logs until bootstrap marker detected
    StartupLogStreamer(client).stream_until_marker()

    # Stage 4: Stream game server pod logs indefinitely
    PodLogMonitor(client).stream_logs()


if __name__ == "__main__":
    main()
