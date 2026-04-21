from meeting_bot_temp import apply_temp_env, cleanup_stale_app_temp


apply_temp_env()
cleanup_stale_app_temp()

from .main import main


if __name__ == "__main__":
    raise SystemExit(main())
