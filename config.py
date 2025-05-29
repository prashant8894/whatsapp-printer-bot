# Default printer settings
DEFAULT_PRINTER_SETTINGS = {
    'color': True,
    'copies': 1,
    'paper_size': 'A4',
    'orientation': 'portrait'
}

# Supported print job types
SUPPORTED_JOB_TYPES = [
    'copy',
    'print',
    'scan'
]

# LLM prompt template for parsing print commands
PRINT_COMMAND_PROMPT = """
You are a printer command parser. Extract print settings from user messages.
Return the settings in the following JSON format:
{
    "job_type": "copy/print/scan",
    "color": true/false,
    "copies": number,
    "file_path": "path to file if applicable"
}
""" 