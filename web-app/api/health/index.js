/**
 * Health check endpoint for Azure Functions (v4)
 */
module.exports = async function (context, req) {
    context.log('Health check endpoint called');

    context.res = {
        status: 200,
        headers: { 
            'Content-Type': 'application/json',
            'Cache-Control': 'no-cache'
        },
        body: JSON.stringify({
            status: 'healthy',
            timestamp: new Date().toISOString(),
            message: 'API is running',
            functionsVersion: '4.x',
            nodeVersion: process.version
        })
    };
};
