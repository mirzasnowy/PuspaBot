"""Public part of Puspa accounts: the local encrypted vault.

Split Step 1: the multi-user account code (``users.py``,
``user_sessions.py``, ``access.py``, ``rls.py``) moved private with
the hosted accounts. What stays public is the vault only — per-user
connector tokens encrypted with a master key from the environment.
The session / user names (``create_session``, ``validate_session``,
``create_user``, ``delete_user``, ``resolve_dsn`` and friends) move
to the SessionStore port in Step 3; they are deliberately NOT
re-exported here.
"""

from .vault import VaultError
from .vault import delete as vault_delete
from .vault import derive_user_key as vault_derive_user_key
from .vault import get as vault_get
from .vault import list_names as vault_list_names
from .vault import put as vault_put
from .vault import rekey_all as vault_rekey_all
from .vault import rotate_entry as vault_rotate_entry

__all__ = [
    "VaultError",
    "vault_delete",
    "vault_derive_user_key",
    "vault_get",
    "vault_list_names",
    "vault_put",
    "vault_rekey_all",
    "vault_rotate_entry",
]
