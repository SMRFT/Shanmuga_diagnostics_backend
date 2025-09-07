from .jwt_check import validateRoleAccess, validateDataAccess

def isAllowed(allowedActionCodes, allowedBranchCodes, pageId, actionId, branchCode):
    return isActionAllowed(allowedActionCodes, pageId, actionId) and isDataAllowed(allowedBranchCodes, branchCode)

def isActionAllowed(allowedActionCodes, pageId, actionId):
    return validateRoleAccess(allowedActionCodes, pageId, actionId)

def isDataAllowed(allowedBranchCodes, branchCode):
    return validateDataAccess(allowedBranchCodes, branchCode)
