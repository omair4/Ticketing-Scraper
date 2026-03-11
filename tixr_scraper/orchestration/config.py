"""
Orchestration configuration settings
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass
class OrchestrationConfig:
    """Configuration for orchestration scripts"""

    # Paths
    state_dir: Path = Path("orchestration/state")
    output_base_dir: Path = Path("output")
    logs_dir: Path = Path("orchestration/logs")

    # Script 2: Bulk scraping — conservative for DataDome
    max_concurrency: int = 3  # Fewer concurrent browsers than PreKindle
    request_delay: float = 5.0  # Longer delay between events
    max_retries_per_event: int = 3

    # Script 3: Inventory refresh
    refresh_concurrency: int = 50  # seats.io CDN doesn't have DataDome
    refresh_delay: float = 0.1

    # Filtering
    skip_sold_out: bool = False
    only_available: bool = False

    # Event discovery
    events_json_path: Path = Path("orchestration/state/events_discovered.json")

    def __post_init__(self):
        if isinstance(self.state_dir, str):
            self.state_dir = Path(self.state_dir)
        if isinstance(self.output_base_dir, str):
            self.output_base_dir = Path(self.output_base_dir)
        if isinstance(self.logs_dir, str):
            self.logs_dir = Path(self.logs_dir)
        if isinstance(self.events_json_path, str):
            self.events_json_path = Path(self.events_json_path)


DEFAULT_ORCHESTRATION_CONFIG = OrchestrationConfig()
