#!/usr/bin/env python
import click
from libcloud.dns.base import Zone
import miniupnpc
import socket
from libcloud.dns.types import Provider, RecordType
from libcloud.dns.providers import get_driver


@click.group()
def main():
    pass

def get_ipv4():
    u = miniupnpc.UPnP()
    u.discoverdelay = 200
    u.discover()
    u.selectigd()
    return u.externalipaddress()


@main.command("update-dns")
def update_dns():
    with open("./token") as f:
        cls = get_driver(Provider.DIGITAL_OCEAN)
        driver = cls(key=f.read().strip())
        ipv4 = get_ipv4()
        zone = driver.get_zone("kiniou.space")
        records = zone.list_records()
        record = next((r for r in records if r.name == "lab"), None)
        if record is not None:
            print(record)
            record.update(type=RecordType.A, data=ipv4, extra={"ttl": 60})
        else:
            zone.create_record(name="lab", type=RecordType.A, data=ipv4, extra={"ttl": 60})



if __name__ == "__main__":
    main()
