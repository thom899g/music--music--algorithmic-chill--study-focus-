"""
Central State Management with Firestore
Architectural Choice: Document-based storage for flexible schema evolution
Alternative considered: PostgreSQL (rejected due to NoSQL better fit for music metadata)
"""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime
import uuid

logger = logging.getLogger(__name__)

class TrackManifest:
    """
    Central state management for track lifecycle with ACID guarantees
    Each track has a single source of truth document in Firestore
    """
    
    def __init__(self, track_uuid: Optional[str] = None):
        """
        Initialize TrackManifest with Firebase instance
        
        Args:
            track_uuid: Optional existing UUID, generates new if None
        """
        from .firebase_setup import firebase_instance
        
        if not firebase_instance.is_initialized:
            raise RuntimeError("Firebase must be initialized before creating TrackManifest")
            
        self.db = firebase_instance.db
        self.track_uuid = track_uuid or str(uuid.uuid4())
        self.collection_ref = self.db.collection('track_manifests')
        self.doc_ref = self.collection_ref.document(self.track_uuid)
        
    def create(self, initial_state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Idempotent manifest creation with transaction safety
        
        Args:
            initial_state: Initial track metadata
        Returns:
            Dict: Created manifest document
        Raises:
            firebase_admin.exceptions.FirebaseError: On Firestore operation failure
        """
        try:
            # Check if manifest already exists
            existing = self.doc_ref.get()
            if existing.exists:
                logger.warning(f"Manifest already exists for UUID: {self.track_uuid}")
                return existing.to_dict()
            
            # Create new manifest with timestamp and version
            manifest = {
                'uuid': self.track_uuid,
                'state': 'PENDING_COMPOSITION',
                'created_at': datetime.utcnow().isoformat(),
                'updated_at': datetime.utcnow().isoformat(),
                'version': 1,
                'history': [{
                    'timestamp': datetime.utcnow().isoformat(),
                    'event': 'MANIFEST_CREATED',
                    'state': 'PENDING_COMPOSITION'
                }],
                **initial_state
            }
            
            # Use transaction for atomic write
            @firestore.transactional
            def create_in_transaction(transaction, doc_ref, manifest_data):
                snapshot = doc_ref.get(transaction=transaction)
                if snapshot.exists:
                    return snapshot.to_dict()
                transaction.set(doc_ref, manifest_data)
                return manifest_data
            
            transaction = self.db.transaction()
            result = create_in_transaction(transaction, self.doc_ref, manifest)
            
            logger.info(f"Created manifest for track: {self.track_uuid}")
            return result
            
        except Exception as e:
            logger.error(f"Failed to create manifest for {self.track_uuid}: {str(e)}")
            raise
    
    def update_state(self, new_state: str, metadata: Optional[Dict] = None) -> bool:
        """
        Update track state with history tracking
        
        Args:
            new_state: New state value
            metadata: Optional additional metadata
        Returns:
            bool: True if update successful
        """
        try:
            # Get current document
            current = self.doc_ref.get()
            if not current.exists:
                logger.error(f"Cannot update non-existent manifest: {self.track_uuid}")
                return False
            
            current_data = current.to_dict()
            
            # Prepare update
            update_data = {
                'state': new_state,
                'updated_at': datetime.utcnow().isoformat(),
                'version': current_data.get('version', 0) + 1
            }
            
            if metadata:
                update_data.update(metadata)
            
            # Add to history
            history_entry = {
                'timestamp': datetime.utcnow().isoformat(),
                'event': 'STATE_CHANGE',
                'old_state': current_data.get('state'),
                'new_state': new_state,
                'metadata': metadata or {}
            }
            
            # Use arrayUnion to append to history
            self.doc_ref.update({
                **update_data,
                'history': firestore.ArrayUnion([history_entry])
            })
            
            logger.info(f"Updated track {self.track_uuid} from {current_data.get('state')} to {new_state}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to update state for {self.track_uuid}: {str(e)}")
            return False
    
    def get_current_state(self) -> Optional[Dict[str, Any]]:
        """
        Get current manifest state
        
        Returns:
            Optional[Dict]: Manifest data or None if not found
        """
        try:
            doc = self.doc_ref.get()
            return doc.to_dict() if doc.exists else None
        except Exception as e:
            logger.error(f"Failed to get state for {self.track_uuid}: {str(e)}")
            return None
    
    def add_error(self, error_message: str, error_code: Optional[str] = None):
        """
        Record error in manifest with retry logic
        
        Args:
            error_message: Human-readable error description
            error_code: Optional error code for programmatic handling
        """
        error_entry = {
            'timestamp': datetime.utcnow().isoformat(),
            'type': 'ERROR',
            'message': error_message,
            'code': error_code
        }
        
        try:
            self.doc_ref.update({
                'errors': firestore.ArrayUnion([error_entry]),
                'last_error': error_message,
                'error_count': firestore.Increment(1)
            })
            
            logger.error(f"Recorded error for track {self.track_uuid}: {error_message}")
            
        except Exception as e:
            logger.error(f"Failed to record error: {str(e)}")

# Track state machine definitions
TRACK_STATES = {
    'PENDING_COMPOSITION': 'Initial state after creation',
    'COMPOSITION_GENERATED': 'Blueprint created successfully',
    'RENDERING_IN_PROGRESS': 'Audio generation in progress',
    'RENDERING_COMPLETE': 'Audio file generated',
    'VALIDATION_FAILED': 'Audio validation failed',
    'UPLOADING': 'Upload to platforms in progress',
    'UPLOAD_COMPLETE': 'Successfully distributed',
    'FAILED': 'Permanent failure state',
    'ARCHIVED': 'Track archived after lifecycle'
}

STATE_TRANSITIONS = {
    'PENDING_COMPOSITION': ['COMPOSITION_GENERATED', 'FAILED'],
    'COMPOSITION_GENERATED': ['RENDERING_IN_PROGRESS', 'FAILED'],
    'RENDERING_IN_PROGRESS': ['RENDERING_COMPLETE', 'VALIDATION_FAILED', 'FAILED'],
    'RENDERING_COMPLETE': ['UPLOADING', 'FAILED'],
    'VALIDATION_FAILED': ['RENDERING_IN_PROGRESS', 'FAILED'],  # Allow retry
    'UPLOADING': ['UPLOAD_COMPLETE', 'FAILED'],
    'UPLOAD_COMPLETE': ['ARCHIVED'],
    'FAILED': ['ARCHIVED'],  # Can archive failed tracks
    'ARCHIVED': []  # Terminal state
}