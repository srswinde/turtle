# -*- coding: utf-8 -*-

"""Global settings for the project"""

import os.path

from tornado.options import define


define("port", default=8000, help="run on the given port", type=int)
define("config", default=None, help="tornado config file")
define("debug", default=False, help="debug mode")

__BASE_PACKAGE__ = "turtle_db"

settings = {}

settings["debug"] = True
settings["cookie_secret"] = "aeV4cFzjlNZXynWMtm5F3eYOK"
settings["login_url"] = "/cassini/login"
settings["static_path"] = os.path.join(os.path.dirname(__file__), __BASE_PACKAGE__, "static")
settings["static_url_prefix"] = "/cassini/static/"
settings["template_path"] = os.path.join(os.path.dirname(__file__), __BASE_PACKAGE__, "templates")
settings["xsrf_cookies"] = False
settings['temp_map'] = {'282abb75d0013cb1': 'ambient', '289e0975d0013ca6':'underground' }