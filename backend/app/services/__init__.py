"""Business logic.

Routers validate, authorise and serialise; jobs schedule. Everything that decides
*what happens* lives here, so the same code path serves an HTTP request and a
scheduled job (ARCHITECTURE.md section 1).
"""
