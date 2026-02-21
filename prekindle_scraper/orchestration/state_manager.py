"""
State management for bulk scraping using JSON file
"""

import json
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
import structlog

from orchestration.config import OrchestrationConfig, DEFAULT_ORCHESTRATION_CONFIG
from orchestration.models import ScrapingStatistics

logger = structlog.get_logger()


class StateManager:
    """Manages scraping state using JSON file"""

    def __init__(self, config: OrchestrationConfig = None):
        self.config = config or DEFAULT_ORCHESTRATION_CONFIG
        self.state_file = self.config.state_dir / "scraping.json"

        # Ensure state directory exists
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

        self._init_state_file()

    def _init_state_file(self):
        """Create state file if it doesn't exist"""
        if not self.state_file.exists():
            initial_state = {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "events": {}
            }
            self._save_state(initial_state)
            logger.info("state_file_initialized", path=str(self.state_file))

    def _load_state(self) -> Dict[str, Any]:
        """Load state from JSON file"""
        if not self.state_file.exists():
            return {"events": {}}

        with open(self.state_file, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _save_state(self, state: Dict[str, Any]):
        """Save state to JSON file"""
        state['updated_at'] = datetime.now(timezone.utc).isoformat()

        with open(self.state_file, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=2, ensure_ascii=False, default=str)

    def initialize_discovered_events(self, events: List[Dict[str, Any]]):
        """
        Initialize state file with discovered events.

        Args:
            events: List of event dicts from PreKindleEventSummary.model_dump()
        """
        state = self._load_state()
        now = datetime.now(timezone.utc).isoformat()

        inserted = 0
        skipped = 0

        for event in events:
            event_id = event.get('event_id', '')
            if not event_id:
                continue

            # Skip if already exists
            if event_id in state['events']:
                skipped += 1
                continue

            # Add new event
            state['events'][event_id] = {
                'event_id': event_id,
                'ticket_url': event.get('ticket_url', ''),
                'title': event.get('title', 'Unknown'),
                'is_sold_out': event.get('is_sold_out', False),
                'purchasable': event.get('purchasable', True),

                # Scraping state
                'status': 'pending',
                'scenario_type': None,
                'scraped_at': None,
                'output_json_path': None,

                # Error tracking
                'error_message': None,
                'retries': 0,

                # Timestamps
                'discovered_at': now,
                'updated_at': now
            }
            inserted += 1

        self._save_state(state)
        logger.info("events_initialized", inserted=inserted, skipped=skipped)
        return inserted

    def get_pending_events(
        self,
        limit: Optional[int] = None,
        skip_sold_out: bool = False,
        only_purchasable: bool = False
    ) -> List[Dict[str, Any]]:
        """Get events with status='pending'"""
        state = self._load_state()
        events = []

        for event_id, event_data in state['events'].items():
            if event_data['status'] != 'pending':
                continue

            if skip_sold_out and event_data.get('is_sold_out', False):
                continue

            if only_purchasable and not event_data.get('purchasable', True):
                continue

            events.append(event_data)

        # Sort by event_id for consistent ordering
        events.sort(key=lambda e: e['event_id'])

        # Apply limit
        if limit:
            events = events[:limit]

        return events

    def get_failed_events(self, max_retries: int = 3) -> List[Dict[str, Any]]:
        """Get failed events that haven't exceeded max retries"""
        state = self._load_state()
        events = []

        for event_id, event_data in state['events'].items():
            if event_data['status'] == 'failed' and event_data.get('retries', 0) < max_retries:
                events.append(event_data)

        events.sort(key=lambda e: e['event_id'])
        return events

    def mark_in_progress(self, event_id: str):
        """Mark event as in_progress"""
        state = self._load_state()

        if event_id in state['events']:
            state['events'][event_id]['status'] = 'in_progress'
            state['events'][event_id]['updated_at'] = datetime.now(timezone.utc).isoformat()
            self._save_state(state)
            logger.debug("event_in_progress", event_id=event_id)

    def mark_completed(
        self,
        event_id: str,
        output_path: str,
        scenario_type: str
    ):
        """Mark event as successfully completed"""
        state = self._load_state()
        now = datetime.now(timezone.utc).isoformat()

        if event_id in state['events']:
            state['events'][event_id].update({
                'status': 'completed',
                'scenario_type': scenario_type,
                'output_json_path': output_path,
                'scraped_at': now,
                'updated_at': now,
                'error_message': None
            })
            self._save_state(state)
            logger.info("event_completed", event_id=event_id, scenario=scenario_type)

    def mark_failed(self, event_id: str, error: str):
        """Mark event as failed and increment retry count"""
        state = self._load_state()
        now = datetime.now(timezone.utc).isoformat()

        if event_id in state['events']:
            state['events'][event_id]['status'] = 'failed'
            state['events'][event_id]['error_message'] = error
            state['events'][event_id]['retries'] = state['events'][event_id].get('retries', 0) + 1
            state['events'][event_id]['updated_at'] = now
            self._save_state(state)
            logger.warning("event_failed", event_id=event_id, error=error)

    def reset_event(self, event_id: str):
        """Reset event to pending status"""
        state = self._load_state()
        now = datetime.now(timezone.utc).isoformat()

        if event_id in state['events']:
            state['events'][event_id]['status'] = 'pending'
            state['events'][event_id]['error_message'] = None
            state['events'][event_id]['updated_at'] = now
            self._save_state(state)
            logger.info("event_reset", event_id=event_id)

    def get_statistics(self) -> ScrapingStatistics:
        """Get scraping progress statistics"""
        state = self._load_state()
        events = state['events'].values()

        total = len(events)
        pending = sum(1 for e in events if e['status'] == 'pending')
        in_progress = sum(1 for e in events if e['status'] == 'in_progress')
        completed = sum(1 for e in events if e['status'] == 'completed')
        failed = sum(1 for e in events if e['status'] == 'failed')

        seatsio_count = sum(1 for e in events if e.get('scenario_type') == 'seatsio')
        ga_count = sum(1 for e in events if e.get('scenario_type') == 'general_admission')
        svg_count = sum(1 for e in events if e.get('scenario_type') == 'svg_seatmap')

        sold_out_count = sum(1 for e in events if e.get('is_sold_out', False))
        purchasable_count = sum(1 for e in events if e.get('purchasable', True))

        return ScrapingStatistics(
            total_events=total,
            pending=pending,
            in_progress=in_progress,
            completed=completed,
            failed=failed,
            seatsio_count=seatsio_count,
            general_admission_count=ga_count,
            svg_seatmap_count=svg_count,
            sold_out_count=sold_out_count,
            purchasable_count=purchasable_count
        )

    def get_event_by_id(self, event_id: str) -> Optional[Dict[str, Any]]:
        """Get single event by ID"""
        state = self._load_state()
        return state['events'].get(event_id)
