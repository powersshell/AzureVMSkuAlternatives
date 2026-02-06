module.exports = async function (context, req) {
    context.log('Health check endpoint called');

    context.res = {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            status: 'healthy',
            timestamp: new Date().toISOString(),
            message: 'API is running',
            functionsVersion: '3.x'
        })
    };
};
