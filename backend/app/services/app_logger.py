import logging
from pathlib import Path

def setup_logger(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger('cyberoffice')
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    logger.addHandler(stream)
    file_handler = logging.FileHandler(log_dir / 'cyberoffice.log', encoding='utf-8')
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)
    return logger
