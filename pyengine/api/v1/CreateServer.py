import pytz
from pyengine.lib.error import *
from pyengine.lib.command import Command

class CreateServer(Command):

    # Request Parameter Info 
    req_params = {
        'name': ('r', 'str'),
        'zone_id': ('r', 'str'),
        'key_name': ('o', 'str'),
        'login_id': ('o', 'str'),
        'stack_id': ('o', 'str'),
        'floatingIP': ('o', 'bool'),
        'request': ('r', 'dic'),
        'register': ('o', 'bool'),
    }
    
    def __init__(self, api_request):
        super(self.__class__, self).__init__(api_request)

    def execute(self):
        mgr = self.locator.getManager('CloudManager')

        ctx = {}
        ctx['user_id'] = self.user_meta['user_id']
        if self.params.has_key('register'):
            info = mgr.registerServer(self.params, ctx)
        else:
            info = mgr.createServer(self.params, ctx)

        e_mgr = self.locator.getManager('EventManager')
        e_mgr.addEvent(self.user_meta['user_id'], 'Create Server(%s) at Zone(%s)' % (info.output['name'],info.output['zone_id']))

        return info.result()
