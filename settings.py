from pydantic import BaseModel, Field
from cat.mad_hatter.decorators import plugin


# settings
class ClawSettings(BaseModel):
    """Settings for the Claw-the-Ollama plugin."""
    
    embedding_model: str = Field(
        default="all-MiniLM-L6-v2",
        title="Embedding Model",
        description="The name of the Ollama embedding model to download and use. This model will be automatically downloaded when settings are saved."
    )
    
    base_url: str = Field(
        default="http://ollama:11434",
        title="Ollama Base URL", 
        description="The base URL where Ollama is running"
    )


# Give your settings model to the Cat.
@plugin
def settings_model():
    return ClawSettings