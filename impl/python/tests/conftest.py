import os

from hypothesis import HealthCheck, settings

# "default": quick and headless; "thorough": for seals (HYPOTHESIS_PROFILE).
settings.register_profile(
    "default", deadline=None, max_examples=60,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
settings.register_profile(
    "thorough", deadline=None, max_examples=600,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))
