from .models import CompanyProfile

def company_profile(request):
 company = CompanyProfile.objects.first()

 return {
    'company_profile': company,
}