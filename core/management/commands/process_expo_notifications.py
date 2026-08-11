import os
import sys
import time
from datetime import datetime
from django.core.management.base import BaseCommand
from core.Views.expo_notifications import process_pending_notifications

class Command(BaseCommand):
    help = 'Processes pending Expo push notifications from milestone_backend_notification collection in MongoDB'

    def add_arguments(self, parser):
        parser.add_argument(
            '--daemon',
            action='store_true',
            help='Run continuously in a loop polling MongoDB every N seconds',
        )
        parser.add_argument(
            '--interval',
            type=int,
            default=5,
            help='Interval in seconds between polls when running in daemon mode (default: 5)',
        )

    def handle(self, *args, **options):
        is_daemon = options['daemon']
        interval = options['interval']

        if is_daemon:
            self.stdout.write(self.style.SUCCESS(f"Starting Expo Notification Background Worker Daemon (Polling every {interval}s)..."))
            tick = 0
            while True:
                try:
                    tick += 1
                    count = process_pending_notifications()
                    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    if count > 0:
                        self.stdout.write(self.style.SUCCESS(f"[{now_str}] [WORKER TICK #{tick}] Successfully processed and pushed {count} notification(s)!"))
                    else:
                        self.stdout.write(f"[{now_str}] [WORKER TICK #{tick}] Polled MongoDB - No pending notifications.")
                except Exception as e:
                    self.stderr.write(self.style.ERROR(f"Error in notification worker loop: {e}"))
                time.sleep(interval)
        else:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.stdout.write(f"[{now_str}] Running one-shot notification processing...")
            count = process_pending_notifications()
            self.stdout.write(self.style.SUCCESS(f"[{now_str}] One-shot execution complete. Processed {count} notification(s)."))
