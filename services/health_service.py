import datetime
import traceback

from flask import Flask
from multiprocessing import Process

app = Flask(__name__)
start_time = datetime.datetime.now()


@app.route('/healthz')
def health():
    # Find the difference between the current time and start_time in seconds
    uptime = (datetime.datetime.now() - start_time).total_seconds()
    # Set the response status
    status = 200
    return {"status": "ok", "uptime": uptime, "uptime_unit": "seconds"}, status


def run_target(host, port):
    try:
        app.run(host=host, port=port, debug=False, use_reloader=False)
    except:
        pass


class HealthService:
    """
    Service for health checks, for cloud services like Azure App Service.
    """

    def __init__(self, host="0.0.0.0", port=8181):
        self.host = host
        self.port = port

        print("Starting the health check service..")
        self.process = Process(target=lambda: run_target(self.host, self.port))
        self.process.start()
        print("Health check service started!")

    def get_process(self):
        return self.process
