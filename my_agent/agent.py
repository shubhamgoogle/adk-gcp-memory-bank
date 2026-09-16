"""Memory-enabled ADK agent backed by Vertex AI Agent Platform Memory Bank.

Long-term memory has two halves, and both are wired up below:

1. WRITE: ``generate_memories_callback`` runs after every agent turn and ships
   the turn's events to Memory Bank, which extracts durable facts in the
   background.
2. READ: ``PreloadMemoryTool`` retrieves relevant memories at the start of each
   turn and injects them into the system instruction.

The memory *service* itself is not constructed here. It is supplied by the
runtime so the same agent works locally and when deployed, e.g.:

    adk web --memory_service_uri=agentengine://<MEMORY_BANK_ID>

Memories are scoped by ``{"app_name": ..., "user_id": ...}``, so different
users never see each other's memories.
"""

from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.llm_agent import Agent
from google.adk.tools.preload_memory_tool import PreloadMemoryTool


async def generate_memories_callback(callback_context: CallbackContext):
    """Sends the events of the turn that just finished to Memory Bank.

    Incremental ingestion is preferred over ``add_session_to_memory()`` so the
    same events are not re-processed on every single turn. The trailing event is
    excluded because it is the in-flight model response for the current turn.

    Memory generation happens asynchronously server-side, so newly stated facts
    typically become retrievable a few seconds later (often on the next turn).
    """
    await callback_context.add_events_to_memory(
        events=callback_context.session.events[-5:-1]
    )
    return None


root_agent = Agent(
    model='gemini-2.5-flash',
    name='root_agent',
    description='A helpful assistant that remembers user preferences.',
    instruction=(
        'Answer user questions to the best of your knowledge.\n'
        'You have long-term memory of previous conversations with this user. '
        'Relevant memories are provided to you automatically — use them to '
        'personalize your answers, and do not ask the user to repeat '
        'preferences they have already shared. Never claim to remember '
        'something that is not in the provided memories.'
    ),
    # Retrieves memories at the start of every turn and adds them to the
    # system instruction. Swap for LoadMemoryTool() to let the model decide
    # when to look memories up instead.
    tools=[PreloadMemoryTool()],
    after_agent_callback=generate_memories_callback,
)
