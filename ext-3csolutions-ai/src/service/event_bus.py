"""
Observer Pattern - Event Bus for Pipeline Status Tracking
Decouples status updates from business logic
"""

from typing import List, Callable, Dict, Any
from dataclasses import dataclass
from datetime import datetime
from ..utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class PipelineEvent:
    """Base class for pipeline events."""
    request_id: str
    timestamp: str
    event_type: str

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat() + 'Z'


@dataclass
class StageStartedEvent(PipelineEvent):
    """Event fired when a pipeline stage starts."""
    stage_name: str

    def __init__(self, request_id: str, stage_name: str):
        super().__init__(
            request_id=request_id,
            timestamp=datetime.utcnow().isoformat() + 'Z',
            event_type='STAGE_STARTED'
        )
        self.stage_name = stage_name


@dataclass
class StageProgressEvent(PipelineEvent):
    """Event fired during stage progress."""
    stage_name: str
    progress: int
    total: int
    message: str
    metadata: Dict[str, Any]

    def __init__(
        self,
        request_id: str,
        stage_name: str,
        progress: int,
        total: int,
        message: str = "",
        metadata: Dict[str, Any] = None
    ):
        super().__init__(
            request_id=request_id,
            timestamp=datetime.utcnow().isoformat() + 'Z',
            event_type='STAGE_PROGRESS'
        )
        self.stage_name = stage_name
        self.progress = progress
        self.total = total
        self.message = message
        self.metadata = metadata or {}


@dataclass
class StageCompletedEvent(PipelineEvent):
    """Event fired when a stage completes successfully."""
    stage_name: str
    duration_seconds: float
    metadata: Dict[str, Any]

    def __init__(
        self,
        request_id: str,
        stage_name: str,
        duration_seconds: float = 0,
        metadata: Dict[str, Any] = None
    ):
        super().__init__(
            request_id=request_id,
            timestamp=datetime.utcnow().isoformat() + 'Z',
            event_type='STAGE_COMPLETED'
        )
        self.stage_name = stage_name
        self.duration_seconds = duration_seconds
        self.metadata = metadata or {}


@dataclass
class StageFailedEvent(PipelineEvent):
    """Event fired when a stage fails."""
    stage_name: str
    error_message: str
    error_type: str
    metadata: Dict[str, Any]

    def __init__(
        self,
        request_id: str,
        stage_name: str,
        error_message: str,
        error_type: str = "Unknown",
        metadata: Dict[str, Any] = None
    ):
        super().__init__(
            request_id=request_id,
            timestamp=datetime.utcnow().isoformat() + 'Z',
            event_type='STAGE_FAILED'
        )
        self.stage_name = stage_name
        self.error_message = error_message
        self.error_type = error_type
        self.metadata = metadata or {}


@dataclass
class FileProcessedEvent(PipelineEvent):
    """Event fired when a file is processed."""
    file_name: str
    file_status: str  # SUCCESS, FAILED, SKIPPED
    metadata: Dict[str, Any]

    def __init__(
        self,
        request_id: str,
        file_name: str,
        file_status: str,
        metadata: Dict[str, Any] = None
    ):
        super().__init__(
            request_id=request_id,
            timestamp=datetime.utcnow().isoformat() + 'Z',
            event_type='FILE_PROCESSED'
        )
        self.file_name = file_name
        self.file_status = file_status
        self.metadata = metadata or {}


class PipelineEventBus:
    """
    Event bus for publishing and subscribing to pipeline events.

    Observer pattern implementation that decouples status tracking
    from business logic.

    Example:
        # Create event bus
        event_bus = PipelineEventBus()

        # Subscribe observers (e.g., DynamoDB tracker, metrics emitter)
        event_bus.subscribe(dynamodb_observer)
        event_bus.subscribe(metrics_observer)

        # Publish events from anywhere in the pipeline
        event_bus.publish(StageStartedEvent(request_id, "Unzip"))
        event_bus.publish(StageCompletedEvent(request_id, "Unzip", duration=5.2))
    """

    def __init__(self):
        """Initialize event bus with empty subscriber list."""
        self._subscribers: List[Callable[[PipelineEvent], None]] = []
        logger.debug("EventBus initialized")

    def subscribe(self, observer: Callable[[PipelineEvent], None]):
        """
        Subscribe an observer to receive events.

        Args:
            observer: Callable that takes a PipelineEvent
        """
        self._subscribers.append(observer)
        logger.debug(f"Observer subscribed: {observer.__name__ if hasattr(observer, '__name__') else 'anonymous'}")

    def unsubscribe(self, observer: Callable[[PipelineEvent], None]):
        """
        Unsubscribe an observer.

        Args:
            observer: Observer to remove
        """
        if observer in self._subscribers:
            self._subscribers.remove(observer)
            logger.debug("Observer unsubscribed")

    def publish(self, event: PipelineEvent):
        """
        Publish an event to all subscribers.

        Args:
            event: Event to publish
        """
        logger.debug(
            f"EventBus: Publishing {event.event_type} for {event.request_id}",
            extra={
                'event_type': event.event_type,
                'request_id': event.request_id
            }
        )

        # Notify all subscribers
        for subscriber in self._subscribers:
            try:
                subscriber(event)
            except Exception as e:
                # Don't let one subscriber failure stop others
                logger.error(
                    f"EventBus: Subscriber failed to handle event: {e}",
                    extra={
                        'event_type': event.event_type,
                        'subscriber': subscriber.__name__ if hasattr(subscriber, '__name__') else 'anonymous',
                        'error': str(e)
                    },
                    exc_info=True
                )

    def clear_subscribers(self):
        """Clear all subscribers (for testing)."""
        self._subscribers.clear()
        logger.debug("EventBus: All subscribers cleared")


# Global event bus instance
_event_bus: PipelineEventBus = None


def get_event_bus() -> PipelineEventBus:
    """
    Get or create the global event bus instance.

    Returns:
        PipelineEventBus instance
    """
    global _event_bus
    if _event_bus is None:
        _event_bus = PipelineEventBus()
    return _event_bus


def reset_event_bus():
    """Reset global event bus (for testing)."""
    global _event_bus
    _event_bus = None
