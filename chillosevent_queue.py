"""
Event Queue System using Firestore as message broker
Architectural Choice: Firestore as queue avoids external dependency on Kafka/RabbitMQ
Trade-off: Polling-based vs push-based (Cloud Functions would be ideal for push)
"""

import logging
import time
import threading
from typing import Dict, Any, Callable, Optional
from datetime import datetime, timedelta
from queue import Queue as ThreadQueue
import uuid

from google.cloud.firestore_v1.base_query import FieldFilter

logger = logging.getLogger(__name__)

class EventQueue:
    """
    Reliable event queue using Firestore with polling consumers
    Implements at-least-once delivery with deduplication
    """
    
    def __init__(self, polling_interval: int = 5):
        """
        Initialize event queue
        
        Args:
            polling_interval: Seconds between polling attempts
        """
        from .firebase_setup import firebase_instance
        
        if not firebase_instance.is_initialized:
            raise RuntimeError("Firebase must be initialized before creating EventQueue")
            
        self.db = firebase_instance.db
        self.polling_interval = polling_interval
        self.consumers = {}
        self.running = False
        self.worker_thread = None
        
        # In-memory queue for local fallback
        self.local_queue = ThreadQueue()
        
    def publish(self, 
                event_type: str, 
                payload: Dict[str, Any], 
                target_service: str,
                priority: int = 0) -> str:
        """
        Publish event to Firestore queue with reliability guarantees
        
        Args:
            event_type: Type of event (e.g., 'TRACK_COMPOSITION_REQUESTED')
            payload: Event payload data
            target_service: Service that should process this event
            priority: Higher priority events processed first
        Returns:
            str: Event ID for tracking
        """
        try:
            event_id = str(uuid.uuid4())
            event_data = {
                'event_id': event_id,
                'type': event_type,
                'payload': payload,
                'target': target_service,
                'priority': priority,
                'created': datetime.utcnow().isoformat(),
                'processed': False,
                'retry_count': 0,
                'last_attempt': None,
                'max_retries': 3,
                'delivery_attempts': []
            }
            
            # Store in Firestore
            self.db.collection('events').document(event_id).set(event_data)
            
            logger.debug(f"Published event {event_id} to {target_service}")
            return event_id
            
        except Exception as e:
            # Fallback to local queue if