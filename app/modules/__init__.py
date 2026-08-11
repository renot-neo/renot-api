"""Domain modules - a Domain-Driven Modular Monolith.

Each module here is self-contained (its own router/schema/model/service/
repository/exceptions) and must not import another module's internals
directly - cross-module communication goes through the service interface in
`module/__init__.py`, or via an event/message.
"""
