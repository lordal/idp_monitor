#!/usr/bin/env python

import cookielib
import importlib
from urlparse import parse_qs
import argparse
from saml2.client import Saml2Client
import sys
from saml2.config import SPConfig
from saml2.s_utils import rndstr
from saml2.samlp import STATUS_SUCCESS
import time
from interaction import Interaction
from interaction import InteractionNeeded
from interaction import Action

__author__ = 'roland'


class Check(object):
    def __init__(self, client, interaction_spec):
        self.client = client
        self.cjar = {"browser": cookielib.CookieJar(),
                     "rp": cookielib.CookieJar(),
                     "service": cookielib.CookieJar()}
        self.interaction = Interaction(self.client, interaction_spec)
        self.features = None

    def my_endpoints(self):
        """
        :returns: All the assertion consumer service endpoints this
            SP publishes.
        """
        return [e for e, b in self.client.config.getattr("endpoints", "sp")[
            "assertion_consumer_service"]]

    def intermit(self, response):
        """
        This method is supposed to handle all needed interactions.
        It also deals with redirects.

        :param response: A response from the IdP
        """
        _response = response
        _last_action = None
        _same_actions = 0
        if _response.status_code >= 400:
            done = True
        else:
            done = False

        url = _response.url
        content = _response.text
        while not done:
            rdseq = []
            while _response.status_code in [302, 301, 303]:
                url = _response.headers["location"]
                if url in rdseq:
                    raise Exception("Loop detected in redirects")
                else:
                    rdseq.append(url)
                    if len(rdseq) > 8:
                        raise Exception(
                            "Too long sequence of redirects: %s" % rdseq)

                # If back to me
                for_me = False
                for redirect_uri in self.my_endpoints():
                    if url.startswith(redirect_uri):
                        # Back at the RP
                        self.client.cookiejar = self.cjar["rp"]
                        for_me = True
                        try:
                            base, query = url.split("?")
                        except ValueError:
                            pass
                        else:
                            _response = parse_qs(query)
                            return _response

                if for_me:
                    done = True
                    break
                else:
                    _response = self.client.send(url, "GET")

                    if _response.status_code >= 400:
                        done = True
                        break

            if done or url is None:
                break

            _base = url.split("?")[0]

            try:
                _spec = self.interaction.pick_interaction(_base, content)
            except InteractionNeeded:
                cnt = content.replace("\n", '').replace("\t", '').replace("\r",
                                                                          '')
                raise Exception(cnt)
            except KeyError:
                cnt = content.replace("\n", '').replace("\t", '').replace("\r",
                                                                          '')
                raise Exception(cnt)

            if _spec == _last_action:
                _same_actions += 1
                if _same_actions >= 3:
                    raise Exception("Interaction loop detection")
            else:
                _last_action = _spec

            _op = Action(_spec["control"])

            try:
                _response = _op(self.client, self, url, _response)
                if isinstance(_response, dict):
                    return _response
                content = _response.text

                if _response.status_code >= 400:
                    txt = "Got status code '%s', error: %s" % (
                        _response.status_code, content)
                    raise Exception(txt)
            except InteractionNeeded:
                raise
            except Exception:
                raise


def check(client, conf, entity_id, supress_output=False):
    check = Check(client, conf.INTERACTION)

    _client = check.client
    relay_state = rndstr()
    _id, htargs = check.client.prepare_for_authenticate(entity_id,
                                                        relay_state=relay_state)
    resp = _client.send(htargs["headers"][0][1], "GET")

    # resp should be dictionary with keys RelayState, SAMLResponse and endpoint
    try:
        resp = check.intermit(resp)
    except Exception, err:
        print "Error"
        print >> sys.stderr, err
    else:
        serv, binding = _client.config.endpoint2service(resp["endpoint"])

        resp = _client.parse_authn_request_response(resp["SAMLResponse"],
                                                    binding)
        assert resp.in_response_to == _id
        try:
            assert resp.response.status.status_code.value == STATUS_SUCCESS
        except AssertionError:
            # Got an error response
            print "Error"
            print >> sys.stderr, resp.response.status
        else:
            if not supress_output:
                print "OK"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', dest='conf_path')
    parser.add_argument('-e', dest='entity_id')
    parser.add_argument('-n', dest='count', default="1")
    parser.add_argument(dest="config")
    args = parser.parse_args()

    #print args
    sys.path.insert(0, ".")
    # If a specific configuration directory is specified look there first
    if args.conf_path:
        sys.path.insert(0, args.conf_path)
    conf = importlib.import_module(args.config)
    sp_config = SPConfig().load(conf.CONFIG, metadata_construction=False)

    client = Saml2Client(sp_config)

    if not args.entity_id:
        # check if there is only one in the metadata store
        entids = client.metadata.items()
        # entids is list of 2-tuples (entity_id, entity description)
        if len(entids) == 1:
            entity_id = entids[0][0]
        else:
            entity_id = args.entity_id
    else:
        entity_id = args.entity_id

    if args.count == "1":
        check(client, conf, entity_id)
    else:
        for i in range(0, int(args.count)):
            check(client, conf, entity_id, supress_output=True)

if __name__ == "__main__":
    start = time.time()
    main()
    print time.time() - start