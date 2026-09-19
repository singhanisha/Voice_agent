import json
import os
import time

class PipelineLogger:
    def __init__(self, log_path="call_logs.jsonl"):
        self.log_path = log_path
        
    def log_event(self, event_type: str, data: dict):
        try:
            entry = {
                "timestamp": time.time(),
                "event": event_type,
                **data
            }
            with open(self.log_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry) + '\n')
        except Exception:
            pass
            
pipeline_logger = PipelineLogger()
