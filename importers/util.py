def find_by_name(items, name):
    result = next((item for item in items if item.name == name), None)
    if result is None:
        raise ValueError(f'"{name}" not found')
    return result
