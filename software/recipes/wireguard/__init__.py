"""WireGuard 配方包，对外暴露三个 Recipe"""
from .recipe import WireGuardRecipe, WgServerRecipe, WgClientRecipe

__all__ = ["WireGuardRecipe", "WgServerRecipe", "WgClientRecipe"]
