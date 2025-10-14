def is_manager(user):    return user.is_authenticated and user.groups.filter(name="manager").exists()
def is_operator(user):   return user.is_authenticated and user.groups.filter(name="operator").exists()
def is_director(user):   return user.is_authenticated and user.groups.filter(name="director").exists()
def can_review(user):    return is_operator(user) or is_director(user)