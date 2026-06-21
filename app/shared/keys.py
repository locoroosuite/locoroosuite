

_user_keys = {}


def set_user_key(user_id, key):
    _user_keys[user_id] = key


def get_user_key(user_id):
    return _user_keys.get(user_id)


def clear_user_key(user_id):
    _user_keys.pop(user_id, None)
