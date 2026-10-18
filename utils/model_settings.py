"""Settings supported by the current chat API and their writable UI."""


def editable_model_settings(model_type):
    return tuple(
        name
        for name in dir(model_type)
        if not name.startswith("_")
        and isinstance(setting := getattr(model_type, name), property)
        and setting.fset is not None
    )
