
import sys
sys.path.insert(0, r"K:\Data_science_projects\MCP_Prompt_Broker\llama-orchestrator\src")
from llama_orchestrator.daemon.service import DaemonService
daemon = DaemonService()
daemon._run_foreground()
