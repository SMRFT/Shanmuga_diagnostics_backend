from rest_framework import permissions
from .jwt_check import isSecurityDisabled, checkAccess, checkAccessForData, checkAccessForPageAction
from .jwt_check import AUD_KEY, NAME_KEY, EMAIL_KEY, PAGE_KEY, ACTION_KEY, PERMISSION_KEY, ALLOWED_ACTIONS_KEY, ALLOWED_DATA_KEY

AUTH_USERID_KEY =  'auth-user-id'
AUTH_USERNAME_KEY =  'auth-user-name'
AUTH_USEREMAIL_KEY =  'auth-user-email'
#AUTH_USERROLE_KEY =  'auth-user-role'
AUTH_BRANCH_CODE_KEY =  'auth-branch-code'
AUTH_PAGE_ID_KEY =  'auth-page-id'
AUTH_ACTION_ID_KEY =  'auth-action-id'
AUTH_PERMISSION_ID_KEY =  'auth-permission-id'
AUTH_ALLOWED_ACTION_CODES_KEY =  'auth-allowed-action-codes'
AUTH_ALLOWED_BRANCH_CODES_KEY =  'auth-allowed-branch-codes'

def _set_auth_data(request, authorizedTokenData, authorizedBranch):
    
    _mutable = None
    if hasattr(request.data, '_mutable'):
        _mutable = request.data._mutable
        request.data._mutable = True
    
    request.data[AUTH_USERID_KEY] = authorizedTokenData[AUD_KEY]
    request.data[AUTH_USERNAME_KEY] = authorizedTokenData[NAME_KEY]
    request.data[AUTH_USEREMAIL_KEY] = authorizedTokenData[EMAIL_KEY]
    #request.data[AUTH_USERROLE_KEY] = authorizedTokenData[ROLE_KEY]
    request.data[AUTH_BRANCH_CODE_KEY] = authorizedBranch
    request.data[AUTH_PAGE_ID_KEY] = authorizedTokenData[PAGE_KEY]
    request.data[AUTH_ACTION_ID_KEY] = authorizedTokenData[ACTION_KEY]
    request.data[AUTH_PERMISSION_ID_KEY] = authorizedTokenData[PERMISSION_KEY]
    request.data[AUTH_ALLOWED_ACTION_CODES_KEY] = authorizedTokenData[ALLOWED_ACTIONS_KEY]
    request.data[AUTH_ALLOWED_BRANCH_CODES_KEY] = authorizedTokenData[ALLOWED_DATA_KEY]
    if _mutable:
        request.data._mutable = _mutable


class HasRoleAndDataPermission(permissions.BasePermission):
    def has_permission(self, request, view) -> bool:
        if isSecurityDisabled():
            print(f'''
                  WARNING: Security disabled in this environment 
                  HasRoleAndDataPermission check skipped
                  auth-user-id & auth-branch-code values not set
                ''')
            return True
        try:
            token = request.headers["Authorization"]
            branch_code = request.headers["Branch-Code"]
            page_path = request.get_full_path()
            http_method = request.method
            vjson = checkAccess(token, branch_code, page_path, http_method)
            _set_auth_data(request, vjson, branch_code)
            print(f'HasRoleAndDataPermission: Access allowed\n')
            return True
        except ValueError as e:
            print(f'HasRoleAndDataPermission: Access not allowed. Reason: {e}\n')
            return False
        except:
            print(f'HasRoleAndDataPermission: Error occured in access validation')
            return False

class HasDataPermission(permissions.BasePermission):
    def has_permission(self, request, view) -> bool:
        if isSecurityDisabled():
            print(f'''
                  WARNING: Security disabled in this environment 
                  HasDataPermission check skipped
                  auth-user-id & auth-branch-code values not set
                ''')
            return True
        try:
            token = request.headers["Authorization"]
            branch_code = request.headers["Branch-Code"]
            vjson = checkAccessForData(token, branch_code)
            _set_auth_data(request, vjson, branch_code)
            print(f'HasDataPermission: Access allowed\n')
            return True
        except ValueError as e:
            print(f'HasDataPermission: Access not allowed. Reason: {e}\n')
            return False
        except:
            print(f'HasDataPermission: Error occured in access validation')
            return False

class HasRolePermission(permissions.BasePermission):
    def has_permission(self, request, view):
        if isSecurityDisabled():
            print(f'''
                  WARNING: Security disabled in this environment 
                  HasRolePermission check skipped
                  auth-user-id value not set
                ''')
            return True
        try:
            token = request.headers["Authorization"]
            page_path = request.get_full_path()
            http_method = request.method
            vjson = checkAccessForPageAction(token, page_path, http_method)
            _set_auth_data(request, vjson, '')
            print(f'HasRolePermission: Access allowed\n')
            return True
        except ValueError as e:
            print(f'HasRolePermission: Access not allowed. Reason: {e}\n')
            return False
        except:
            print(f'HasRolePermission: Error occured in access validation')
            return False