import logging
import os
from datetime import datetime
from pathlib import Path

def setup_logger(name: str, log_dir: str = "logs") -> logging.Logger:
    """
    Set up a logger that writes to a timestamped file in the log directory.
    Filename format: YYYY-MM-DD_HH-mm-ss_Fehlerhaftes {name}.log
    """
    # Create log directory if it doesn't exist
    log_path = Path(log_dir)
    log_path.mkdir(exist_ok=True)
    
    # Generate timestamp
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    # Ensure name is safe for filename
    safe_name = "".join(c for c in name if c.isalnum() or c in (" ", "_", "-")).rstrip()
    filename = f"{timestamp}_Fehlerhaftes {safe_name}.log"
    log_file = log_path / filename
    
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    
    # Avoid adding multiple handlers
    if not logger.handlers:
        # File handler
        fh = logging.FileHandler(log_file, encoding='utf-8')
        fh.setLevel(logging.DEBUG)
        # Console handler
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        # Formatter
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        fh.setFormatter(formatter)
        ch.setFormatter(formatter)
        logger.addHandler(fh)
        logger.addHandler(ch)
    
    return logger