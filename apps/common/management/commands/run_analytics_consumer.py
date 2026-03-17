"""Analytics Kafka consumer -- reads order.events and inventory.events topics
and updates Prometheus counters.

Run as a standalone management command:
    python manage.py run_analytics_consumer
"""

from __future__ import annotations

import json
import logging
import signal

from django.conf import settings
from django.core.management.base import BaseCommand
from prometheus_client import Counter, Gauge

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------
ORDERS_PLACED_TOTAL = Counter(
    "eventshop_orders_placed_total",
    "Total number of OrderPlaced events consumed",
)
ORDERS_CANCELLED_TOTAL = Counter(
    "eventshop_orders_cancelled_total",
    "Total number of OrderCancelled events consumed",
)
PAYMENTS_CONFIRMED_TOTAL = Counter(
    "eventshop_payments_confirmed_total",
    "Total number of PaymentConfirmed events consumed",
)
STOCK_RESERVED_TOTAL = Counter(
    "eventshop_stock_reserved_total",
    "Total units of stock reserved",
    ["sku"],
)
STOCK_RELEASED_TOTAL = Counter(
    "eventshop_stock_released_total",
    "Total units of stock released",
    ["sku"],
)
CONSUMER_LAG = Gauge(
    "eventshop_consumer_lag",
    "Consumer lag (messages behind)",
    ["topic"],
)


class Command(BaseCommand):
    help = "Run the analytics Kafka consumer that tracks order and inventory metrics"

    def add_arguments(self, parser):
        parser.add_argument(
            "--group-id",
            default="analytics-consumer",
            help="Kafka consumer group ID",
        )
        parser.add_argument(
            "--poll-timeout",
            type=float,
            default=1.0,
            help="Poll timeout in seconds",
        )

    def handle(self, *args, **options):
        try:
            from confluent_kafka import Consumer, KafkaError
        except ImportError:
            self.stderr.write("confluent-kafka is not installed")
            return

        self._running = True
        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

        conf = {
            "bootstrap.servers": settings.KAFKA_BOOTSTRAP_SERVERS,
            "group.id": options["group_id"],
            "auto.offset.reset": "earliest",
            "enable.auto.commit": True,
        }

        consumer = Consumer(conf)
        topics = [
            settings.KAFKA_TOPIC_ORDER_EVENTS,
            settings.KAFKA_TOPIC_INVENTORY_EVENTS,
        ]
        consumer.subscribe(topics)
        self.stdout.write(f"Subscribed to topics: {topics}")

        poll_timeout = options["poll_timeout"]

        while self._running:
            msg = consumer.poll(timeout=poll_timeout)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                logger.error("Consumer error: %s", msg.error())
                continue

            try:
                value = json.loads(msg.value().decode())
                self._process_event(value, msg.topic())
            except Exception:
                logger.exception(
                    "Failed to process message from %s", msg.topic(),
                )

        consumer.close()
        self.stdout.write("Consumer shut down gracefully")

    def _shutdown(self, signum, frame):
        self.stdout.write("Shutting down consumer...")
        self._running = False

    def _process_event(self, event: dict, topic: str) -> None:
        event_type = event.get("event_type", "")
        payload = event.get("payload", {})

        if event_type == "OrderPlaced":
            ORDERS_PLACED_TOTAL.inc()
            logger.info("OrderPlaced: order=%s", payload.get("order_id"))

        elif event_type == "OrderCancelled":
            ORDERS_CANCELLED_TOTAL.inc()
            logger.info("OrderCancelled: order=%s", payload.get("order_id"))

        elif event_type == "PaymentConfirmed":
            PAYMENTS_CONFIRMED_TOTAL.inc()
            logger.info("PaymentConfirmed: order=%s", payload.get("order_id"))

        elif event_type == "StockReserved":
            sku = payload.get("sku", "unknown")
            qty = payload.get("qty", 0)
            STOCK_RESERVED_TOTAL.labels(sku=sku).inc(qty)
            logger.info("StockReserved: sku=%s qty=%d", sku, qty)

        elif event_type == "StockReleased":
            sku = payload.get("sku", "unknown")
            qty = payload.get("qty", 0)
            STOCK_RELEASED_TOTAL.labels(sku=sku).inc(qty)
            logger.info("StockReleased: sku=%s qty=%d", sku, qty)

        else:
            logger.debug("Unknown event type: %s", event_type)
