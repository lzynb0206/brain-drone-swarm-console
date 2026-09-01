"""Firebase-backed infrastructure adapters."""

from src.adapters.firebase.auth_gateway import FirebaseAuthGateway, FirebaseSession

__all__ = ["FirebaseAuthGateway", "FirebaseSession"]
