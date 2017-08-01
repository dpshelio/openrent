#!/usr/bin/python3

import argparse
import urllib.request
import json
import datetime
import requests
from time import sleep

from bs4 import BeautifulSoup
from urllib.parse import urlencode
from collections import OrderedDict
from get_url import parse_property_page, parse_property_page_sr, property_filepath, property_filepath_sr
from slackclient import SlackClient
import trolly

import re


def mdlinks(text):
    return re.sub(r'\<(.+?)\|(.+?)\>', r'[\2](\1)', text)


def create_card(title, description):
    description = mdlinks(description)
    description = description.replace("*", "**")
    c2 = l2.add_card({'name': title, "desc": description})


import os

with open(os.path.join(os.path.dirname(os.path.realpath(__file__)),
                       "config.json")) as f:
    config = json.load(f)
    sc_token = config["slack_token"]
    tr_token = config["trello_token"]
    tr_key = config["trello_key"]
    tr_board = config["trello_board"]
    work_addr1 = config["work_addr1"]
    work_addr2 = config["work_addr2"]
    radius = config.get("radius", 20)
    areas = [work_addr1, work_addr2]
    # searchid1 = config["sr_searchid1"]
    # searchid2 = config.get("sr_searchid2", None)
    # searchids = [searchid1, searchid2]
    max_value = config.get("max_value", 1500)
    min_value = config.get("min_value", 1000)
    avail_from = config.get("avail_from", datetime.datetime.today())
    avail_from = datetime.datetime.strptime(avail_from, "%Y-%m-%d") if not isinstance(avail_from, datetime.datetime) else avail_from
    delta = datetime.timedelta(days=config.get("delta_days", 30))

sc = SlackClient(sc_token)

client = trolly.client.Client(tr_key, tr_token)
b2 = client.get_board(tr_board) # househunting
b2.update_board()
l2 = [_ for _ in b2.get_lists()][0] # first list on the left
l2.update_list()


def directions_link(prop):
    def maps_link(start_addr, end_addr):
        query_string = urlencode(
            OrderedDict(f="d",
                        saddr=start_addr,
                        daddr=end_addr,
                        dirflg="r"))

        return "http://maps.google.co.uk/?%s" % query_string

    if 'latlong' in prop:
        start_addr = prop["latlong"]
    else:
        start_addr = ",".join(prop['title'].split(",")[1:])

    return "*Directions* 1: <{}|to {}> and 2: <{}|to {}>".format(
        maps_link(start_addr, work_addr1), work_addr1,
        maps_link(start_addr, work_addr2), work_addr2)


def links_filepath():
    outdir = os.path.dirname(os.path.realpath(__file__))
    return os.path.join(outdir, 'links.json')


def should_notify(prop):
    price = prop['price']
    title = prop['title']
    desc = prop['description']
    epc = prop['EPC']
    try:
        av = datetime.datetime.strptime(prop['available_from'], '%Y-%m-%d')
    except ValueError:
        av = datetime.datetime.today()

    if price > max_value:
        return False, "too expensive: {} > {}".format(price, max_value)
    if price < min_value:
        return False, "too cheap: {} < {}".format(price, min_value)

    if "Note: This OpenRent Property Is No Longer Available For Rent" in desc:
        return False, "already let"

    # if "studio" in desc.lower():
    #     return False, "studio"

    # if "studio" in title.lower():
    #     return False, "studio"

    if "shared flat" in desc.lower():
        return False, "shared flat"

    if "shared flat" in title.lower():
        return False, "shared flat"

    if epc and (epc.upper() in list("EFG")):
        return False, "EPC is too low: {}".format(epc.upper())

    if av < avail_from:
        return False, "Available date ({:%Y-%m-%d}) is too early".format(av)
    if av > avail_from + delta:
        return False, "Available date ({:%Y-%m-%d}) is too late".format(av)

    return True, ""


def notify(property_id):
    print("Notifying about %s..." % property_id)

    def make_link(property_id):
        return ("https://www.openrent.co.uk/%s" % property_id)

    sc.api_call("api.test")
    sc.api_call("channels.info", channel="1234567890")

    with open(property_filepath(property_id)) as f:
        prop = json.load(f)

    should_notify_, reason = should_notify(prop)
    if not should_notify_:
        print("Skipping notification: %s..." % reason)
        return

    if not len(prop['location']) > 0:
        prop['location'].append(['unknown'] * 2)
    text = ("<{link}|{title}> close to {location} ({walk_duration}):\n"
            "*Price:* {price}. *Available from:* {av}. *EPC:* {epc}. {has_garden}\n"
            "{directions}.\n*Description:*\n{desc}").format(
        location=prop['location'][0][0],
        walk_duration=prop['location'][0][1],
        link=make_link(property_id),
        price=prop['price'],
        desc=prop['description'][:1000],
        av=prop['available_from'],
        title=prop['title'],
        epc=prop['EPC'],
        directions=directions_link(prop),
        has_garden="With garden. " if prop['has_garden'] else "")

    sc.api_call("chat.postMessage", channel="#general",
                text=text, username='propertybot',
                icon_emoji=':new:')
    create_card("{} - {}".format(prop['title'], prop['price']), text)


def update_list(should_notify=True, area=work_addr1):
    query_string = urlencode(
        OrderedDict(term=area,
                    within=str(radius),
                    prices_min=min_value,
                    prices_max=max_value,
                    bedrooms_min=0,
                    bedrooms_max=3,
                    isLive="true"))

    url = ("http://www.openrent.co.uk/properties-to-rent/?%s" % query_string)

    html_doc = urllib.request.urlopen(url).read()
    soup = BeautifulSoup(html_doc, 'html.parser')

    if os.path.isfile(links_filepath()):
        with open(links_filepath()) as f:
            existing_links = json.load(f)
    else:
        existing_links = {}

    with open(links_filepath(), 'w') as f:
        latest_links = [x['href'][1:] for x
                        in soup.find_all("a", class_="banda pt")]
        print("Received %s property links..." % len(latest_links))
        latest_and_old = list(set(latest_links) | set(existing_links.get('openrent', [])))
        all_data = existing_links.copy()
        all_data['openrent'] = latest_and_old
        json.dump(all_data, f, indent=4)

    new_links = list(set(latest_links) - set(existing_links.get('openrent',[])))
    print("Found %s new links!..." % len(new_links))

    for property_id in new_links:
        test = parse_property_page(property_id)
        if should_notify and test is not None:
            notify(property_id)
        else:
            print("Found a property %s but notifications are disabled."
                  % property_id)


def notify_sr(property_id):
    print("Notifying about %s..." % property_id)

    def make_link(property_id):
        return ("https://www.spareroom.co.uk/%s" % property_id)

    sc.api_call("api.test")
    sc.api_call("channels.info", channel="1234567890")

    with open(property_filepath_sr(property_id)) as f:
        prop = json.load(f)

    should_notify_, reason = should_notify(prop)
    if not should_notify_:
        print("Skipping notification: %s..." % reason)
        return

    if not len(prop['location']) > 0:
        prop['location'].append(['unknown'] * 2)
    text = ("<{link}|{title}> close to {location} ({walk_duration}):\n"
            "*Price:* {price:.2f}. *Available from:* {av}. *EPC:* {epc}. {has_garden}\n"
            "{directions}.\n*Description:*\n{desc}").format(
        location=prop['location'][0][0],
        walk_duration=prop['location'][0][1],
        link=make_link(property_id),
        price=prop['price'],
        desc=prop['description'][:1000],
        av=prop['available_from'],
        title=prop['title'],
        epc=prop['EPC'],
        directions=directions_link(prop),
        has_garden="With garden. " if prop['has_garden'] else "")

    sc.api_call("chat.postMessage", channel="#general",
                text=text, username='propertybot',
                icon_emoji=':new:')
    create_card("{} - {:.2f}".format(prop['title'], prop['price']), text)


def update_list_sr(should_notify=True, area=work_addr1, search_id=None):

    headers = {'User-Agent': 'SpareRoomUK 3.1'}
    cookies = {'session_id': '00000000', 'session_key': '000000000000000'}
    api_location = 'http://iphoneapp.spareroom.co.uk'
    api_search_endpoint = 'flatshares'
    api_details_endpoint = 'flatshares'

    def make_get_request(url=None, headers=None, cookies=None, proxies=None, sleep_time=0.3):
        # if DEBUG:
        print('Sleeping for {secs} seconds'.format(secs=sleep_time))
        sleep(sleep_time)
        return requests.get(url, cookies=cookies, headers=headers).text

    if search_id is None:
        params = OrderedDict(format='json',
                             max_rent=max_value,
                             per='pcm',
                             page=1,
                             max_per_page=100,
                             where=area.lower(),
                             miles_from_max=str(radius),
                             posted_by="private_landlords",
                             showme_1beds='Y',
                             available_from='{:%Y-%m-%d}'.format(avail_from),
                             )

    else:
        params = OrderedDict(format='json',
                             search_id=search_id,
                             page=1)

    sr_results = list()
    page = 1
    total_pages = 100
    pages_left = 100
    while pages_left:
        url = '{location}/{endpoint}?{params}'.format(location=api_location,
                                                      endpoint=api_search_endpoint,
                                                      params=urlencode(params))
        try:
            results = json.loads(make_get_request(url=url,
                                                  cookies=cookies,
                                                  headers=headers))
            page = results['page']
            total_pages = results['pages']
            pages_left = total_pages - page
            sr_results.extend(results['results'])
            # if VERBOSE:
            #     print('Parsing page {page}/{total} flats in {area}'.format(page=results['page'], total=results['pages'], area=area))
        except Exception as e:
            print(e)
            return None
        params['page'] += 1

    # Add results to the list
    if os.path.isfile(links_filepath()):
        with open(links_filepath()) as f:
            existing_links = json.load(f)
    else:
        existing_links = {}

    with open(links_filepath(), 'w') as f:
        latest_links = [r['advert_id'] for r in sr_results]
        print("Received %s property links..." % len(latest_links))
        print(" saved links {}".format(len(set(existing_links.get('spareroom', [])))))
        latest_and_old = list(set(latest_links) | set(existing_links.get('spareroom',[])))
        all_data = existing_links.copy()
        all_data['spareroom'] = latest_and_old
        json.dump(all_data, f, indent=4)

    new_links = list(set(latest_links) - set(existing_links.get('spareroom', [])))
    print("Found %s new links!..." % len(new_links))
    new_links = [r for r in sr_results if r['advert_id'] in new_links]

    # === Check each property and create notification
    for property_prop in new_links:
        property_id = property_prop['advert_id']
        test = parse_property_page_sr(property_prop)
        if should_notify and test is not None:
            notify_sr(property_id)
        else:
            print("Found a property %s but notifications are disabled."
                  % property_id)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--nonotify", help="don't notify", action='store_true',
                        default=False)
    args = parser.parse_args()

    should_notify_ = not args.nonotify
    if not os.path.isfile(links_filepath()):
        should_notify_ = False
        print("No links.json detected. This must be the first run: not"
              " notifying about all suitable properties.")
    for area in areas:
        update_list(should_notify=should_notify_, area=area)
        update_list_sr(should_notify=should_notify_, area=area)
# TODO: MAX TERM IN SR
