const { app } = require('@azure/functions');

app.http('health', {
    methods: ['GET'],
    authLevel: 'anonymous',
    handler: async (request, context) => {
        context.log('Health check endpoint called');
        
        return {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
            jsonBody: {
                status: 'healthy',
                timestamp: new Date().toISOString(),
                message: 'API is running'
            }
        };
    }
});
