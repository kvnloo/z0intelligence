import Base
import ./WorkItem.bend as WorkItem

# ABI identity for adapter.mjs. Every transition decision is WorkItem.astep, unchanged.
# Work-item machine only: the worker/device machines are not shipped on this page.
def abi_version() -> U32:
  1
