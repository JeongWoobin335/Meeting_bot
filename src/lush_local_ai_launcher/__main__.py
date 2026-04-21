"""Command line entrypoint for the local AI launcher."""

from meeting_bot_temp import apply_temp_env, cleanup_stale_app_temp

apply_temp_env()
cleanup_stale_app_temp()

from .launcher import main


if __name__ == "__main__":
    main()
