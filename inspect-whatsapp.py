from Cocoa import NSWorkspace
from ApplicationServices import (
    AXUIElementCreateApplication,
    AXUIElementCopyAttributeValue,
    kAXRoleAttribute,
    kAXTitleAttribute,
    kAXDescriptionAttribute,
    kAXValueAttribute,
    kAXChildrenAttribute,
)

BUNDLE_ID = "net.whatsapp.WhatsApp"


def get_whatsapp_pid():
    """
    Cherche le processus WhatsApp actuellement lancé
    et retourne son PID.
    """
    apps = NSWorkspace.sharedWorkspace().runningApplications()

    for running_app in apps:
        if running_app.bundleIdentifier() == BUNDLE_ID:
            return running_app.processIdentifier()

    return None


def get_attr(element, attribute):
    """
    Lit un attribut Accessibility d'un élément.
    Retourne None si l'attribut n'existe pas ou en cas d'erreur.
    """
    try:
        error, value = AXUIElementCopyAttributeValue(
            element,
            attribute,
            None
        )

        if error == 0:
            return value

    except Exception:
        pass

    return None


def walk(element, depth=0, max_depth=10):
    """
    Parcourt récursivement l'arbre Accessibility
    à partir d'un élément.
    """

    if depth > max_depth:
        return

    role = get_attr(element, kAXRoleAttribute)
    title = get_attr(element, kAXTitleAttribute)
    description = get_attr(element, kAXDescriptionAttribute)
    value = get_attr(element, kAXValueAttribute)

    indent = "  " * depth

    print(
        f"{indent}"
        f"role={role!r} | "
        f"title={title!r} | "
        f"description={description!r} | "
        f"value={value!r}"
    )

    children = get_attr(element, kAXChildrenAttribute)

    if not children:
        return

    for child in children:
        walk(
            child,
            depth=depth + 1,
            max_depth=max_depth
        )


def main():
    pid = get_whatsapp_pid()

    if pid is None:
        print("❌ WhatsApp n'est pas lancé.")
        print("Ouvre WhatsApp puis relance ce script.")
        return

    print(f"✅ WhatsApp trouvé — PID : {pid}")

    # Ici on crée l'objet Accessibility racine de WhatsApp.
    #
    # C'est CET objet qui est ensuite appelé "app".
    app = AXUIElementCreateApplication(pid)

    print(f"✅ Objet Accessibility créé : {app}")
    print()
    print("===== ARBRE ACCESSIBILITY WHATSAPP =====")
    print()

    walk(app)


if __name__ == "__main__":
    main()