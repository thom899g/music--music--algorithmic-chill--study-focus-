"""
Firebase Core Infrastructure Setup
Architectural Choice: Firebase provides serverless scalability with real-time capabilities
Alternative considered: AWS S3 + DynamoDB (rejected due to complexity for MVP)
"""

import os
import json
import logging
from typing import Dict, Optional, Any
from datetime import datetime

# Third-party imports
import firebase_admin
from firebase_admin import credentials, firestore, storage
from firebase_admin.exceptions import FirebaseError

logger = logging.getLogger(__name__)

class FirebaseInitializer:
    """Robust Firebase initialization with error recovery and fallback states"""
    
    def __init__(self, service_account_path: Optional[str] = None):
        """
        Initialize Firebase with comprehensive error handling
        
        Args:
            service_account_path: Path to Firebase service account JSON
        Raises:
            FileNotFoundError: If service account file doesn't exist
            ValueError: If Firebase initialization fails
        """
        self.service_account_path = service_account_path
        self._db = None
        self._bucket = None
        self._initialized = False
        
    def initialize(self) -> bool:
        """
        Idempotent Firebase initialization with graceful degradation
        
        Returns:
            bool: True if initialization successful, False with fallback mode
        """
        # Check if already initialized
        if self._initialized:
            logger.info("Firebase already initialized, skipping")
            return True
            
        # Validate service account file exists
        if not self.service_account_path:
            self.service_account_path = self._discover_service_account()
            
        if not os.path.exists(self.service_account_path):
            logger.error(f"Service account file not found: {self.service_account_path}")
            return self._enable_fallback_mode()
        
        try:
            # Load and validate service account JSON
            with open(self.service_account_path, 'r') as f:
                service_account = json.load(f)
            
            required_keys = ['type', 'project_id', 'private_key_id', 'private_key']
            missing_keys = [key for key in required_keys if key not in service_account]
            
            if missing_keys:
                logger.error(f"Invalid service account: missing keys {missing_keys}")
                return self._enable_fallback_mode()
            
            # Initialize Firebase app
            cred = credentials.Certificate(self.service_account_path)
            firebase_admin.initialize_app(cred, {
                'storageBucket': f"{service_account['project_id']}.appspot.com"
            })
            
            # Initialize components
            self._db = firestore.client()
            self._bucket = storage.bucket()
            self._initialized = True
            
            logger.info(f"Firebase initialized successfully for project: {service_account['project_id']}")
            return True
            
        except (FirebaseError, json.JSONDecodeError, IOError) as e:
            logger.error(f"Firebase initialization failed: {str(e)}")
            return self._enable_fallback_mode()
    
    def _discover_service_account(self) -> str:
        """Attempt to locate service account file in common locations"""
        possible_paths = [
            "chillos-service-account.json",
            "../chillos-service-account.json",
            "./config/chillos-service-account.json",
            os.path.join(os.path.expanduser("~"), ".chillos", "service-account.json")
        ]
        
        for path in possible_paths:
            if os.path.exists(path):
                logger.info(f"Discovered service account at: {path}")
                return path
        
        return "chillos-service-account.json"  # Default expectation
    
    def _enable_fallback_mode(self) -> bool:
        """
        Enable local fallback mode for development/offline scenarios
        
        Returns:
            bool: Always False to indicate fallback mode
        """
        logger.warning("Enabling fallback mode - using local storage only")
        self._db = None
        self._bucket = None
        self._initialized = False
        return False
    
    @property
    def db(self):
        """Get Firestore client with null check"""
        if self._db is None:
            raise RuntimeError("Firebase not initialized. Call initialize() first.")
        return self._db
    
    @property
    def bucket(self):
        """Get Storage bucket with null check"""
        if self._bucket is None:
            raise RuntimeError("Firebase not initialized. Call initialize() first.")
        return self._bucket
    
    @property
    def is_initialized(self) -> bool:
        """Check if Firebase is properly initialized"""
        return self._initialized

# Global singleton instance
firebase_instance = FirebaseInitializer()