"""Erro da curadoria, num módulo leve: a API o importa sem trazer o `fuse` do builder."""


class ErroCuradoria(ValueError):
    """Pedido recusado; o texto é o que a pessoa vê."""
