import requests
import time
import subprocess
from typing import Optional


def check_health(url: str, threshold: int, interval: int) -> bool:
    """
    Check the health of a URL. Returns True if the service is healthy,
    False otherwise.
    """
    try:
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            return data.get("status") == "ok"
    except requests.RequestException:
        pass

    return False


def restart_service():
    """
    Restarts the service by running a series of system commands.
    """
    commands = [
        "cd /home/kaveen/GPTDiscord",
        "kill -9 $(cat bot.pid)",
        "rm bot.pid",
        "screen -dmS GPTBot python3.9 gpt3discord.py",
    ]
    full_command = "; ".join(commands)
    subprocess.run(full_command, shell=True, check=True)


def monitor_service(url: str, threshold: int = 3, interval: int = 30):
    """
    Monitors a service at the given URL, restarting it if it fails
    the health check consecutively for a given threshold number of times.
    """
    failure_count = 0

    while True:
        if not check_health(url, threshold, interval):
            failure_count += 1
            print(f"Health check failed {failure_count} times")
        else:
            failure_count = 0

        if failure_count >= threshold:
            print(f"Restarting service after {failure_count} consecutive failures.")
            try:
                restart_service()
            except subprocess.SubprocessError as e:
                print(f"Error restarting service: {e}")
            finally:
                failure_count = 0

        time.sleep(interval)


if __name__ == "__main__":
    # Adjust the threshold and interval as needed
    monitor_service("http://localhost:8181/healthz", threshold=30, interval=10)
