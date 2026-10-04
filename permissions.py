# permissions.py

from ApplicationServices import (
    AXIsProcessTrustedWithOptions,
    kAXTrustedCheckOptionPrompt,
)

trusted = AXIsProcessTrustedWithOptions({
    kAXTrustedCheckOptionPrompt: True
})

print("Accessibility autorisé :", bool(trusted))