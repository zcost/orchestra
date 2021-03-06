import pytz
from pyengine.lib.error import *
from pyengine.lib.command import Command

class UpdateRegion(Command):

    # Request Parameter Info 
    req_params = {
        'name': ('o', 'str'),
    }
    
    def __init__(self, api_request):
        super(self.__class__, self).__init__(api_request)

    def execute(self):
        mgr = self.locator.getManager('CloudManager')

        info = mgr.updateRegion(self.params)

        return info.result()
