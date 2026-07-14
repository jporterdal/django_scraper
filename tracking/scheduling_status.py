def schedules_may_not_fire() -> bool:
    """True when the schedules UI should warn that runs may not fire."""
    from django.conf import settings

    redis_url = (getattr(settings, "REDIS_URL", None) or "").strip()
    immediate = bool(settings.HUEY.get("immediate", False))
    return (not redis_url) or immediate
