import logging
import queue


class QueueHandler(logging.Handler):
    """Logging handler that adds log entry to queue"""

    def __init__(self, log_queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record):
        """Add log entry to queue"""
        try:
            message = self.format(record)
            if self.log_queue.full():
                self.log_queue.get_nowait()
            self.log_queue.put_nowait(message)
        except Exception:
            self.handleError(record)


class LogQueueManager:
    """Manager that starts/stops/outputs queued logger messages"""

    def __init__(self, max_size=100):
        self.max_size = max_size
        self.log_queue = None
        self.queue_handler = None
        self.logger = None
        self.active = False


    def start(self):
        """Attach a queue handler to root logger"""
        if self.active:
            return
        self.log_queue = queue.Queue(maxsize=self.max_size)
        self.queue_handler = QueueHandler(self.log_queue)
        self.queue_handler.setFormatter(logging.Formatter(
            "{asctime} - {levelname} [{module}]: {message}",
            style="{",
            datefmt="%Y-%m-%d-%H:%M:%S",
        ))
        self.logger = logging.getLogger()
        self.logger.addHandler(self.queue_handler)
        self.active = True


    def stop(self):
        """Remove queue handler from root logger"""
        if not self.active:
            return
        if self.logger and self.queue_handler in self.logger.handlers:
            self.logger.removeHandler(self.queue_handler)
        self.queue_handler = None
        if self.log_queue.full():
            self.log_queue.get_nowait()
        self.log_queue.put_nowait(None)
        self.active = False


    def get_log_entry(self):
        """Get log entry from queue"""
        if not self.active or not self.log_queue:
            return None
        return self.log_queue.get(block=True)


def read_log_file(path, limit=100):
    """
    Read log file and reformat {asctime} - {levelname}/n  [{module}]: {message}/n
    into {asctime} - {levelname} [{module}]: {message}
    """
    log = []
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().split("\n\n")
    for line in lines:
        sublines = line.split("\n")
        output_line = ""
        for num, subline in enumerate(sublines):
            if subline.startswith("  ["):
                output_line += " " + subline[2:]
            else:
                output_line += "\n" + subline
        if output_line.strip():
            log.append(output_line.strip())
    return log[-limit:]
