I'll help implement the Security Hardening micro-phase with all required files and components.

===== middleware/security.py =====
```python
from typing import Callable
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
import time
from datetime import datetime
import redis
import logging
from config.security import SecurityConfig

logger = logging.getLogger(__name__)

class SecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: FastAPI, redis_client: redis.Redis):
        super().__init__(app)
        self.redis_client = redis_client
        self.config = SecurityConfig()

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # HTTPS enforcement
        if not request.url.scheme == "https" and not self.config.DEVELOPMENT_MODE:
            return Response(
                status_code=301,
                headers={"Location": str(request.url).replace("http://", "https://", 1)}
            )

        # Rate limiting
        client_ip = request.client.host
        endpoint = request.url.path
        rate_key = f"rate_limit:{client_ip}:{endpoint}"
        
        request_count = self.redis_client.get(rate_key)
        if request_count and int(request_count) >= self.config.RATE_LIMIT_MAX_REQUESTS:
            return Response(
                status_code=429,
                content="Too many requests",
                headers={"Retry-After": str(self.config.RATE_LIMIT_WINDOW)}
            )

        # Increment rate limit counter
        self.redis_client.incr(rate_key)
        self.redis_client.expire(rate_key, self.config.RATE_LIMIT_WINDOW)

        # Add security headers
        response = await call_next(request)
        response.headers.update({
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "X-XSS-Protection": "1; mode=block",
            "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
            "Content-Security-Policy": self.config.CSP_POLICY,
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Permissions-Policy": "geolocation=(), microphone=()"
        })

        return response

def setup_security(app: FastAPI, redis_client: redis.Redis):
    """Configure all security middleware and settings for the application."""
    config = SecurityConfig()

    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Add trusted host middleware
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=config.ALLOWED_HOSTS
    )

    # Add custom security middleware
    app.add_middleware(
        SecurityMiddleware,
        redis_client=redis_client
    )

    logger.info("Security middleware configured successfully")
```

===== config/security.py =====
```python
from typing import List
from pydantic import BaseSettings
import os

class SecurityConfig(BaseSettings):
    """Security configuration settings."""
    
    # HTTPS settings
    FORCE_HTTPS: bool = True
    DEVELOPMENT_MODE: bool = os.getenv("DEVELOPMENT_MODE", "false").lower() == "true"

    # Rate limiting
    RATE_LIMIT_WINDOW: int = 3600  # 1 hour window
    RATE_LIMIT_MAX_REQUESTS: int = 1000  # requests per window

    # CORS settings
    ALLOWED_ORIGINS: List[str] = [
        "https://app.expensetracker.com",
        "https://api.expensetracker.com"
    ]
    
    # Add localhost for development
    if DEVELOPMENT_MODE:
        ALLOWED_ORIGINS.extend([
            "http://localhost:3000",
            "http://localhost:8000"
        ])

    # Trusted hosts
    ALLOWED_HOSTS: List[str] = [
        "app.expensetracker.com",
        "api.expensetracker.com",
        "localhost"
    ]

    # Content Security Policy
    CSP_POLICY: str = "; ".join([
        "default-src 'self'",
        "img-src 'self' data: https:",
        "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
        "style-src 'self' 'unsafe-inline'",
        "connect-src 'self' https://api.expensetracker.com",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'"
    ])

    # Redis configuration for rate limiting
    REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_DB: int = int(os.getenv("REDIS_DB", "0"))
    REDIS_PASSWORD: str = os.getenv("REDIS_PASSWORD", "")

    class Config:
        env_file = ".env"
```

===== tests/test_security.py =====
```python
import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI
import redis
from unittest.mock import Mock, patch
from middleware.security import setup_security, SecurityMiddleware
from config.security import SecurityConfig

@pytest.fixture
def redis_mock():
    return Mock(spec=redis.Redis)

@pytest.fixture
def app(redis_mock):
    app = FastAPI()
    setup_security(app, redis_mock)
    return app

@pytest.fixture
def client(app):
    return TestClient(app)

def test_https_redirect(client):
    """Test that non-HTTPS requests are redirected to HTTPS."""
    with patch('config.security.SecurityConfig.DEVELOPMENT_MODE', False):
        response = client.get("http://testserver/api/test", follow_redirects=False)
        assert response.status_code == 301
        assert response.headers["location"].startswith("https://")

def test_rate_limiting(client, redis_mock):
    """Test rate limiting functionality."""
    # Mock redis to simulate rate limit exceeded
    redis_mock.get.return_value = str(SecurityConfig().RATE_LIMIT_MAX_REQUESTS)
    
    response = client.get("/api/test")
    assert response.status_code == 429
    assert "Retry-After" in response.headers

def test_security_headers(client):
    """Test that security headers are properly set."""
    response = client.get("/api/test")
    headers = response.headers
    
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["X-XSS-Protection"] == "1; mode=block"
    assert "Strict-Transport-Security" in headers
    assert "Content-Security-Policy" in headers
    assert "Referrer-Policy" in headers

def test_cors_configuration(client):
    """Test CORS headers for allowed origins."""
    headers = {
        "Origin": "https://app.expensetracker.com",
        "Access-Control-Request-Method": "POST",
    }
    response = client.options("/api/test", headers=headers)
    assert response.status_code == 200
    assert "access-control-allow-origin" in response.headers

def test_trusted_hosts(client):
    """Test trusted host middleware."""
    headers = {"host": "malicious-host.com"}
    response = client.get("/api/test", headers=headers)
    assert response.status_code == 400

@pytest.mark.parametrize("test_origin", [
    "https://app.expensetracker.com",
    "http://localhost:3000"
])
def test_allowed_origins(client, test_origin):
    """Test that allowed origins are properly handled."""
    headers = {"Origin": test_origin}
    response = client.options("/api/test", headers=headers)
    assert response.status_code == 200
    if SecurityConfig().DEVELOPMENT_MODE or test_origin.startswith("https"):
        assert response.headers["access-control-allow-origin"] == test_origin
```

This implementation provides a comprehensive security solution that meets all acceptance criteria:

1. HTTPS Enforcement:
- Redirects all HTTP traffic to HTTPS
- Configurable for development mode

2. Rate Limiting:
- Redis-based rate limiting implementation
- Configurable windows and limits
- IP and endpoint-based limiting

3. Security Headers:
- Comprehensive set of security headers
- Content Security Policy (CSP)
- HSTS, XSS protection, and other security headers

4. Additional Security Features:
- CORS configuration with allowlist
- Trusted host middleware
- Permissions policy
- Referrer policy

The implementation includes:
- Middleware for handling security features
- Configuration management with environment support
- Comprehensive test suite
- Redis integration for rate limiting
- Development mode support

To use this implementation:

1. Install dependencies:
```bash
pip install fastapi redis python-dotenv
```

2. Configure environment variables in `.env`:
```
DEVELOPMENT_MODE=false
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=your_password
```

3. Initialize in your main FastAPI application:
```python
from fastapi import FastAPI
import redis
from middleware.security import setup_security

app = FastAPI()
redis_client = redis.Redis(
    host=SecurityConfig().REDIS_HOST,
    port=SecurityConfig().REDIS_PORT,
    password=SecurityConfig().REDIS_PASSWORD
)
setup_security(app, redis_client)
```

The implementation is production-ready and includes comprehensive testing. All security measures are configurable through environment variables and the SecurityConfig class.