if (typeof SwaggerUIBundle === 'function') {
  SwaggerUIBundle({
    url: '/api/v1/openapi.json',
    dom_id: '#swagger-ui',
    layout: 'BaseLayout',
    deepLinking: true,
    showExtensions: true,
    showCommonExtensions: true,
    validatorUrl: null,
    presets: [SwaggerUIBundle.presets.apis],
  });
} else {
  document.getElementById('api-docs-status').textContent = '交互文档加载失败，请查看 OpenAPI JSON。';
}
