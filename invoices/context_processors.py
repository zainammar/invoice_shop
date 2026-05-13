from .models import CompanyProfile

def company_profile(request):
    """Make company profile available in all templates"""
    if request.user.is_authenticated:
        profile, _ = CompanyProfile.objects.get_or_create(
            user=request.user,
            defaults={'company_name': request.user.username + "'s Company"}
        )
        return {'company_profile': profile}
    return {'company_profile': None}
