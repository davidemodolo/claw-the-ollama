from cat.mad_hatter.decorators import hook
from cat.mad_hatter.decorators import plugin

from cat.log import log
from cat.db import crud
import json
import requests
import time
import os
import threading
from typing import Dict, List, Any, Optional, cast
from cat.looking_glass.stray_cat import StrayCat


def normalize_url(base_url: str = "http://ollama:11434") -> str:
    """Normalize the API URL format"""
    base_url = base_url.rstrip('/')
    if not base_url.startswith('http'):
        base_url = f"http://{base_url}"
    return base_url


def check_model_exists(model: str, base_url: str = "http://ollama:11434") -> bool:
    """Check if the specified Ollama model already exists"""
    try:
        base_url = normalize_url(base_url)
        
        # Get the list of installed models
        response = requests.get(f"{base_url}/api/tags")
        
        if response.status_code != 200:
            log.warning(f"Failed to get model list: {response.status_code}")
            return False
        
        models_data: Dict[str, List[Dict[str, Any]]] = response.json()
        installed_models: List[str] = [
            cast(str, model_data.get('name')) 
            for model_data in models_data.get('models', [])
        ]
        
        # Check if our model is in the list
        return model in installed_models
        
    except Exception as e:
        log.error(f"Error checking if model exists: {e}")
        return False


def notify(message: str, cat: Optional[StrayCat] = None) -> None:
    """Helper function to log and send websocket messages"""
    log.info(message)
    if cat:
        cat.send_ws_message(message)


def pull_ollama_model(model: str, cat: Optional[StrayCat] = None, base_url: str = "http://ollama:11434") -> bool:
    """Pull the specified Ollama model if it doesn't exist"""
    try:
        base_url = normalize_url(base_url)
        
        # Check if model already exists
        if check_model_exists(model, base_url):
            log.info(f"Model {model} is already installed, skipping download")
            return True
            
        # Send initial notification
        notify(f"I'm downloading the {model} model... This might take a few minutes ⏳", cat)
        
        # Start the model pull request
        response: requests.Response = requests.post(
            f"{base_url}/api/pull",
            json={"name": model},
            stream=True,
        )
        
        if response.status_code != 200:
            raise Exception(f"Failed to pull model: {response.status_code}")
        
        time_last_notification: float = time.time()
        
        for line in response.iter_lines():
            if not line:
                continue
                
            try:
                data: Dict[str, Any] = json.loads(line.decode('utf-8'))
                status: str = data.get('status', '')

                # Send notification every 5 seconds
                if time.time() - time_last_notification > 5:
                    if 'completed' in data and 'total' in data:
                        percentage: float = (data['completed'] / data['total']) * 100
                        message: str = f"Still downloading {model}... {percentage:.0f}% complete 📥"
                    else:
                        message: str = f"Still downloading {model}... 📥"
                    
                    notify(message, cat)
                    time_last_notification = time.time()
                
                if status == "success":
                    success_msg: str = f"Great! {model} is now ready to use! 🎉"
                    notify(success_msg, cat)
                    return True
                elif "error" in data:
                    error_msg = data.get("error", "Unknown error")
                    log.error(f"Ollama download error: {error_msg}")
                    if cat:
                        cat.send_ws_message(f"Download failed: {error_msg}")
                    return False
                    
            except json.JSONDecodeError:
                continue
        
        return True
        
    except Exception as e:
        error_msg: str = f"Error pulling Ollama model '{model}': {e}"
        log.error(error_msg)
        if cat:
            cat.send_ws_message(f"Sorry, I couldn't download the model: {e}")
        return False

def save_plugin_settings_to_file(settings: Dict, plugin_path: str) -> Dict:
    """
    Save plugin settings to settings.json file in the plugin directory.
    This replicates the default save behavior from the Cat framework.
    
    Args:
        settings: The settings dictionary to save
        plugin_path: The path to the plugin directory
        
    Returns:
        The updated settings dictionary, or empty dict if save failed
    """
    settings_file_path = os.path.join(plugin_path, "settings.json")
    
    # Load already saved settings (replicate load_settings behavior)
    old_settings = {}
    if os.path.exists(settings_file_path):
        try:
            with open(settings_file_path, "r") as json_file:
                old_settings = json.load(json_file)
        except Exception as e:
            log.error(f"Unable to load existing settings: {e}")
    
    # Merge new settings with old ones
    updated_settings = {**old_settings, **settings}
    
    # Save settings to file
    try:
        with open(settings_file_path, "w") as json_file:
            json.dump(updated_settings, json_file, indent=4)
        return updated_settings
    except Exception as e:
        log.error(f"Unable to save plugin settings: {e}")
        return {}

@plugin
def save_settings(settings: Dict):
    # Log the settings that are being saved
    log.warning(f"Settings saved: {settings}")
    
    # Try to pull the model if it's specified in settings
    try:
        embedding_model = settings.get("embedding_model")
        if embedding_model:
            # Get base_url from settings, default to standard Ollama URL
            base_url = settings.get("base_url", "http://ollama:11434")
            log.info(f"Scheduling background download for model: {embedding_model} from {base_url}")
            
            # Run model download in background thread to avoid blocking the settings save
            def download_model_async():
                try:
                    success = pull_ollama_model(embedding_model, None, base_url)
                    if success:
                        log.info(f"Background download completed for model: {embedding_model}")
                    else:
                        log.warning(f"Background download failed for model: {embedding_model}")
                except Exception as e:
                    log.error(f"Error in background model download: {e}")
            
            # Start the download in a daemon thread
            download_thread = threading.Thread(target=download_model_async, daemon=True)
            download_thread.start()
            log.info(f"Background download thread started for model: {embedding_model}")
        else:
            log.info("No embedding_model specified in settings")
    except Exception as e:
        log.error(f"Error trying to schedule model download during settings save: {e}")

    # Save settings using the extracted function
    plugin_path = os.path.dirname(os.path.abspath(__file__))
    return save_plugin_settings_to_file(settings, plugin_path)

@hook  # default priority = 1 
def before_cat_reads_message(user_message_json: Dict[str, Any], cat: StrayCat) -> Dict[str, Any]:
    try:
        # Get all settings at once
        settings: Dict[str, Dict[str, Any]] = {s.get("name"): s for s in crud.get_settings()}
        # Check if Ollama is selected
        llm_selected: Dict[str, Any] = settings.get("llm_selected", {})
        ollama_config: Dict[str, Any] = settings.get("LLMOllamaConfig", {})
        
        if (llm_selected.get("value", {}).get("name") == "LLMOllamaConfig" and ollama_config):
            # Extract model name and base URL
            config_value: Dict[str, Any] = ollama_config.get("value", {})
            model: Optional[str] = config_value.get("model")
            
            if model:
                log.info(f"Detected Ollama configuration with model: {model}")
                
                # Get Ollama API URL from settings if available
                ollama_url: str = config_value.get("base_url", "http://ollama:11434")
                
                # Pull the model
                pull_ollama_model(model, cat, ollama_url)
            else:
                log.warning("Ollama configuration found but model name is missing")
        else:
            log.info("Non-Ollama LLM provider is selected or configuration is missing")
    
    except Exception as e:
        log.error(f"Error in Ollama autopull plugin: {e}")
    
    return user_message_json

@hook
def before_cat_bootstrap(cat: StrayCat) -> None:
    try:
        # Get all settings at once
        settings: Dict[str, Dict[str, Any]] = {s.get("name"): s for s in crud.get_settings()}
        
        # Check embedder configuration
        embedder_selected: Dict[str, Any] = settings.get("embedder_selected", {})
        embedder_config: Dict[str, Any] = settings.get("EmbedderOllamaConfig", {})
        
        if (embedder_selected.get("value", {}).get("name") == "EmbedderOllamaConfig" and embedder_config):
            # Extract model name and base URL
            config_value: Dict[str, Any] = embedder_config.get("value", {})
            model: Optional[str] = config_value.get("model")
            
            if model:
                log.info(f"Detected Ollama embedder configuration with model: {model}")
                
                # Get Ollama API URL from settings if available
                ollama_url: str = config_value.get("base_url", "http://ollama:11434")
                
                # Pull the model (without websocket messages since UI isn't ready)
                pull_ollama_model(model, None, ollama_url)
            else:
                log.warning("Ollama embedder configuration found but model name is missing")
        else:
            log.info("Non-Ollama embedder provider is selected or configuration is missing")
    
    except Exception as e:
        log.error(f"Error in Ollama embedder autopull during bootstrap: {e}")