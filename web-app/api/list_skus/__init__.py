"""
Azure Function to list available VM SKUs from cache
Used to populate dropdown in frontend
"""
import logging
import json
import os
import azure.functions as func
from azure.data.tables import TableServiceClient
from azure.identity import DefaultAzureCredential


def main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Processing list SKUs request')
    
    # Get location from query parameters
    location = req.params.get('location')
    
    if not location:
        return func.HttpResponse(
            json.dumps({'error': 'location parameter is required'}),
            mimetype='application/json',
            status_code=400
        )
    
    # Get storage account name from environment
    storage_account_name = os.environ.get('SKU_CACHE_STORAGE_ACCOUNT')
    
    if not storage_account_name:
        return func.HttpResponse(
            json.dumps({'error': 'SKU cache not configured'}),
            mimetype='application/json',
            status_code=500
        )
    
    try:
        # Initialize Table Service with managed identity
        credential = DefaultAzureCredential()
        table_service = TableServiceClient(
            endpoint=f"https://{storage_account_name}.table.core.windows.net",
            credential=credential
        )
        
        table_client = table_service.get_table_client("vmskus")
        
        # Query all SKUs for the location
        query_filter = f"PartitionKey eq '{location}'"
        entities = table_client.query_entities(query_filter=query_filter)
        
        # Format for frontend dropdown
        skus = []
        for entity in entities:
            skus.append({
                'name': entity['name'],
                'displayName': f"{entity['name']} ({entity['vCPUs']} vCPUs, {entity['memoryGB']} GB)",
                'vCPUs': entity['vCPUs'],
                'memoryGB': entity['memoryGB'],
                'hourlyPrice': entity.get('hourlyPrice', 0),
                'monthlyPrice': entity.get('monthlyPrice', 0),
                'currency': entity.get('currency', 'USD'),
                'gpuCount': entity.get('gpuCount', 0)
            })
        
        # Sort by vCPUs then memory
        skus.sort(key=lambda x: (x['vCPUs'], x['memoryGB']))
        
        response_data = {
            'location': location,
            'count': len(skus),
            'skus': skus
        }
        
        return func.HttpResponse(
            json.dumps(response_data),
            mimetype='application/json',
            status_code=200
        )
        
    except Exception as e:
        logging.error(f'Error listing SKUs: {e}')
        return func.HttpResponse(
            json.dumps({
                'error': 'Failed to retrieve SKU list',
                'details': str(e)
            }),
            mimetype='application/json',
            status_code=500
        )
