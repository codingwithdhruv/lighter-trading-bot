import json
import os
from utils.logger import logger
from utils.config import PACIFICA_API_KEY, PACIFICA_SUBACCOUNT
from utils.config import DECIBEL_PRIVATE_KEY, DECIBEL_NODE_API_KEY

SETTINGS_FILE = "data/copy_settings.json"

class ConfigManager:
    def __init__(self):
        # Pacifica settings
        self.pacifica_enabled = True
        self.pacifica_leverage = 40
        self.pacifica_max_loss_usd = 20.0
        # Decibel settings (mirrors Pacifica)
        self.decibel_enabled = True
        self.decibel_leverage = 40
        self.decibel_max_loss_usd = 20.0
        # UI Closure Tracking
        self.track_ui_closures = False
        self.load()

    def load(self):
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r") as f:
                    data = json.load(f)
                    self.pacifica_enabled = data.get("pacifica_enabled", self.pacifica_enabled)
                    self.pacifica_leverage = data.get("pacifica_leverage", self.pacifica_leverage)
                    self.pacifica_max_loss_usd = data.get("pacifica_max_loss_usd", self.pacifica_max_loss_usd)
                    self.decibel_enabled = data.get("decibel_enabled", self.decibel_enabled)
                    self.decibel_leverage = data.get("decibel_leverage", self.decibel_leverage)
                    self.decibel_max_loss_usd = data.get("decibel_max_loss_usd", self.decibel_max_loss_usd)
                    self.track_ui_closures = data.get("track_ui_closures", self.track_ui_closures)
            except Exception as e:
                logger.error(f"Failed to load copy settings: {e}")

    def save(self):
        try:
            os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
            with open(SETTINGS_FILE, "w") as f:
                json.dump({
                    "pacifica_enabled": self.pacifica_enabled,
                    "pacifica_leverage": self.pacifica_leverage,
                    "pacifica_max_loss_usd": self.pacifica_max_loss_usd,
                    "decibel_enabled": self.decibel_enabled,
                    "decibel_leverage": self.decibel_leverage,
                    "decibel_max_loss_usd": self.decibel_max_loss_usd,
                    "track_ui_closures": self.track_ui_closures
                }, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to save copy settings: {e}")

    def toggle_pacifica(self) -> bool:
        self.pacifica_enabled = not self.pacifica_enabled
        self.save()
        return self.pacifica_enabled

    def toggle_decibel(self) -> bool:
        self.decibel_enabled = not self.decibel_enabled
        self.save()
        return self.decibel_enabled
        
    def toggle_track_ui_closures(self) -> bool:
        self.track_ui_closures = not self.track_ui_closures
        self.save()
        return self.track_ui_closures
        
    def update_setting(self, key: str, value):
        if hasattr(self, key):
            setattr(self, key, value)
            self.save()
            return True
        return False

config_manager = ConfigManager()
