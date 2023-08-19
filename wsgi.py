from main import app
from tasks import schedule_remove_expired_contacts, enqueue_jobs
import logging

logging.basicConfig(filename='flasklogs.log', level=logging.INFO,
                    format='%(asctime)s %(levelname)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

logging.info("Server starting...")

enqueue_jobs()
schedule_remove_expired_contacts()


logging.info("Successfully started all saved jobs")


if __name__ == "__main__":
    app.run()
