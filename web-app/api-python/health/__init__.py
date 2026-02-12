"""
Health check endpoint for Azure Functions (Python)
"""
import logging
import json
import datetime
import sys
import azure.functions as func


def main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Health check endpoint called')

    response_data = {
        'status': 'healthy',
        'timestamp': datetime.datetime.utcnow().isoformat() + 'Z',
        'message': 'API is running',
        'runtime': 'Python',
        'pythonVersion': sys.version
    }

    return func.HttpResponse(
        json.dumps(response_data),
        mimetype='application/json',
        status_code=200
    )
