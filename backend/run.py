import uvicorn
from app.config import settings

if __name__ == "__main__":
    print(f"\nIniciando servidor {settings.PROJECT_NAME}...")
    print(f"URL API: http://localhost:{settings.PORT}")
    print(f"Documentación interactiva Swagger: http://localhost:{settings.PORT}/docs\n")
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG
    )
